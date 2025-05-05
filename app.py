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
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import logging
import json
from google_auth_oauthlib.flow import InstalledAppFlow

# Configure logging to file
logging.basicConfig(
    filename='app.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s:%(message)s'
)

# OAuth Drive scopes
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

st.sidebar.header("Resume Source")
mode = st.sidebar.radio("Select source:", ["Upload Files", "From Google Drive"])

# Session-state for resume paths
if 'paths' not in st.session_state:
    st.session_state['paths'] = []
paths = st.session_state['paths']

if mode == "Upload Files":
    uploaded = st.sidebar.file_uploader(
        "Upload one or more resume files", type=['pdf','docx'], accept_multiple_files=True
    )
    if st.sidebar.button("Load Uploaded Files"):
        if not uploaded:
            st.sidebar.error("Please upload at least one resume.")
        else:
            tmp = tempfile.mkdtemp()
            paths.clear()
            for f in uploaded:
                p = os.path.join(tmp, f.name)
                with open(p, 'wb') as out:
                    out.write(f.read())
                paths.append(p)
            st.sidebar.success(f"Uploaded {len(paths)} files.")
            st.session_state['paths'] = paths
            logging.info(f"Uploaded {len(paths)} files via upload.")
else:
    # OAuth-only Drive load with console flow
    creds_file = st.sidebar.file_uploader(
        "Upload OAuth client_secret.json", type='json', key='oauth_creds'
    )
    folder_id = st.sidebar.text_input(
        "Drive Folder ID", key='oauth_folder_id'
    )
    if st.sidebar.button("Load Resumes from Drive"):
        if not creds_file or not folder_id:
            st.sidebar.error(
                "Please upload OAuth client_secret.json and enter Drive Folder ID."
            )
        else:
            try:
                config = json.loads(creds_file.getvalue().decode('utf-8'))
                if 'installed' not in config and 'web' not in config:
                    st.sidebar.error(
                        "Invalid OAuth JSON format. Please provide a client_secret.json."
                    )
                else:
                    flow = InstalledAppFlow.from_client_config(config, SCOPES)
                    creds = flow.run_console()
                    svc = build('drive', 'v3', credentials=creds)
                    q = (
                        f"'{folder_id}' in parents and "
                        "(mimeType='application/pdf' or "
                        "mimeType='application/vnd.openxmlformats-officedocument.wordprocessingml.document')"
                    )
                    files = svc.files().list(
                        q=q, fields='files(id,name)'
                    ).execute().get('files', [])
                    tmp = tempfile.mkdtemp()
                    paths.clear()
                    logging.info(f"Found {len(files)} resumes in Drive folder {folder_id}")
                    for idx, f in enumerate(files, start=1):
                        req = svc.files().get_media(fileId=f['id'])
                        fh = BytesIO()
                        downloader = MediaIoBaseDownload(fh, req)
                        done = False
                        while not done:
                            status, done = downloader.next_chunk()
                            logging.info(f"Downloading resume {idx}/{len(files)}: {f['name']}")
                        p = os.path.join(tmp, f['name'])
                        with open(p, 'wb') as out:
                            out.write(fh.getvalue())
                        paths.append(p)
                    st.sidebar.success(f"Loaded {len(paths)} resumes from Drive.")
                    st.session_state['paths'] = paths
                    logging.info(f"Successfully loaded {len(paths)} resumes")
            except Exception as e:
                st.sidebar.error(f"Drive load failed: {e}")
                logging.error(f"Drive load failed: {e}")

st.sidebar.header("Matching Settings")
# ... rest of matching logic follows, unchanged ...
