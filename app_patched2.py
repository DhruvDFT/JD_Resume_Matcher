import streamlit as st
import os
import re
import pandas as pd
import tempfile
from io import BytesIO
from pdfminer.high_level import extract_text as pdf_extract
import zipfile
import xml.etree.ElementTree as ET
from docx import Document
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import logging
import smtplib
from email.message import EmailMessage

# --- Logging Setup ---
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Extraction Utilities ---
EMAIL_REGEX = r"[\w\.-]+@[\w\.-]+\.[a-zA-Z]{2,}"
PHONE_REGEX = r"\+?\d[\d \-]{7,}\d"

def extract_text_from_file(path):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == '.pdf':
            return pdf_extract(path)
        elif ext == '.docx':
            try:
                doc = Document(path)
                return '\n'.join(p.text for p in doc.paragraphs)
            except Exception:
                text = ''
                with zipfile.ZipFile(path) as z:
                    xml = z.read('word/document.xml')
                tree = ET.fromstring(xml)
                for node in tree.iter():
                    if node.tag.endswith('}t') or node.tag == 't':
                        if node.text:
                            text += node.text + ' '
                return text
    except Exception as e:
        logger.warning(f"Error extracting text from {os.path.basename(path)}: {e}")
    return ''

def extract_contacts(text):
    emails = re.findall(EMAIL_REGEX, text)
    phones = re.findall(PHONE_REGEX, text)
    name = ''
    if emails:
        user = emails[0].split('@')[0]
        parts = re.split(r'[\._]', user)
        name = ' '.join(p.capitalize() for p in parts if p)
    return name, ', '.join(emails), ', '.join(phones)

# --- Matching Logic ---
def run_matching(paths, keywords, exp_req, relax, domains, tools, skillsets):
    total = len(paths)
    logger.info(f"Resumes to process: {total}")
    progress = st.progress(0)
    records = []
    for i, path in enumerate(paths, 1):
        text = extract_text_from_file(path)
        exp_nums = [float(m) for m in re.findall(r"(\d+(?:\.\d+)?)\s*(?:years|yrs|year)", text, flags=re.IGNORECASE)]
        experience = max(exp_nums) if exp_nums else 0.0
        matched_kw = [kw for kw in keywords if re.search(re.escape(kw), text, flags=re.IGNORECASE)]
        match_pct = round(len(matched_kw)/len(keywords)*100, 2) if keywords else 0.0
        matched_domains = [d for d in domains if re.search(re.escape(d), text, flags=re.IGNORECASE)]
        matched_tools   = [t for t in tools    if re.search(re.escape(t), text, flags=re.IGNORECASE)]
        matched_skills  = [s for s in skillsets if re.search(re.escape(s), text, flags=re.IGNORECASE)]
        exp_ok = True
        if exp_req is not None:
            req = exp_req - 1 if (relax and exp_req > 0) else exp_req
            exp_ok = experience >= req
        name, email_addr, phone = extract_contacts(text)
        records.append({
            'Filename': os.path.basename(path),
            'Name': name,
            'Email': email_addr,
            'Phone': phone,
            'Domain': ';'.join(matched_domains),
            'Tools': ';'.join(matched_tools),
            'Skillset': ';'.join(matched_skills),
            'Experience_Years': experience,
            'Experience_Match': exp_ok,
            'Matched_Keywords': ';'.join(matched_kw),
            'Match_Percentage': match_pct
        })
        progress.progress(i/total)
    return pd.DataFrame(records)

# --- Email Sending ---
def send_report_via_email(smtp_server, smtp_port, sender, password, recipient, df):
    msg = EmailMessage()
    msg['Subject'] = 'JD-Resume Matching Report'
    msg['From'] = sender
    msg['To'] = recipient
    body = f"Please find attached the JD-Resume matching report.\nTotal: {len(df)}, Matched: {df['Experience_Match'].sum()}"
    msg.set_content(body)
    buf = BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    msg.add_attachment(buf.read(),
                       maintype='application',
                       subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                       filename='match_report.xlsx')
    try:
        with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
            server.login(sender, password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise ValueError("Application-specific password required. Create one at https://myaccount.google.com/apppasswords")

# --- Streamlit App ---
st.title("📄 JD–Resume Matcher & Dashboard")

st.sidebar.header("Data Source")
mode = st.sidebar.selectbox("Source:", ["Upload Files", "Google Drive"])
paths = []
if mode == "Upload Files":
    uploaded = st.sidebar.file_uploader("Upload resume files", type=['pdf','docx'], accept_multiple_files=True)
    if uploaded:
        tmp = tempfile.mkdtemp()
        for f in uploaded:
            p = os.path.join(tmp, f.name)
            with open(p, 'wb') as out: out.write(f.getbuffer())
            paths.append(p)
        st.sidebar.success(f"Uploaded {len(paths)} files.")
else:
    creds_file  = st.sidebar.file_uploader("Upload Drive JSON", type='json')
    folder_id   = st.sidebar.text_input("Drive Folder ID")
    if creds_file and folder_id:
        creds = service_account.Credentials.from_service_account_info(
            pd.read_json(BytesIO(creds_file.getvalue()), typ='series').to_dict(),
            scopes=['https://www.googleapis.com/auth/drive.readonly']
        )
        svc = build('drive','v3',credentials=creds)
        q = f"'{folder_id}' in parents and (mimeType='application/pdf' or mimeType='application/vnd.openxmlformats-officedocument.wordprocessingml.document')"
        files = svc.files().list(q=q, fields='files(id,name)').execute().get('files',[])
        tmp = tempfile.mkdtemp()
        for f in files:
            req = svc.files().get_media(fileId=f['id'])
            fh = BytesIO(); dl = MediaIoBaseDownload(fh, req); done=False
            while not done:
                status, done = dl.next_chunk()
            p = os.path.join(tmp, f['name']); open(p,'wb').write(fh.getvalue()); paths.append(p)
        st.sidebar.success(f"Loaded {len(paths)} from Drive.")        

st.sidebar.header("Email Report (optional)")
send_email = st.sidebar.checkbox("Send report via email")
if send_email:
    smtp      = st.sidebar.text_input("SMTP server", "smtp.gmail.com")
    port      = st.sidebar.number_input("SMTP port", 465)
    sender    = st.sidebar.text_input("Sender email")
    pwd       = st.sidebar.text_input("Email password", type="password")
    recipient = st.sidebar.text_input("Recipient email")

st.sidebar.header("Matching Criteria")
keywords  = [k.strip() for k in st.sidebar.text_input("Keywords", "Verilog,SystemVerilog,TCL").split(',') if k.strip()]
domains   = [d.strip() for d in st.sidebar.text_input("Domains", "VLSI").split(',') if d.strip()]
tools     = [t.strip() for t in st.sidebar.text_input("Tools", "Synopsys,Cadence").split(',') if t.strip()]
skillsets = [s.strip() for s in st.sidebar.text_input("Skillset", "Verilog,Python").split(',') if s.strip()]
exp_req   = st.sidebar.number_input("Min experience (years)", min_value=0.0, value=5.0, step=0.5)
relax     = st.sidebar.checkbox("Relax requirement by 1 year (5+ → 4+)")

if st.button("Run Matching"):
    if not paths:
        st.error("Please provide resumes first.")
    else:
        df = run_matching(paths, keywords, exp_req, relax, domains, tools, skillsets)
        total   = len(df)
        matched = int(df['Experience_Match'].sum())
        avg_pct = df['Match_Percentage'].mean()
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Resumes", total)
        c2.metric("Matched", matched)
        c3.metric("Avg. Match %", f"{avg_pct:.2f}")
        st.bar_chart(df['Match_Percentage'])
        st.subheader("Matching Profile Details")
        st.dataframe(df[df['Experience_Match']])
        if send_email:
            try:
                send_report_via_email(smtp, port, sender, pwd, recipient, df)
                st.success("Email sent successfully.")
            except ValueError as ve:
                st.error(str(ve))
            except Exception as e:
                st.error(f"Email failed: {e}")
        buf = BytesIO(); df.to_excel(buf, index=False); buf.seek(0)
        st.download_button("Download as XLSX", buf, file_name="match_report.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
