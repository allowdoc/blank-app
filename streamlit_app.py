import streamlit as st
import subprocess
import threading

# Function to run the background script
def run_troy_script():
    subprocess.Popen(["python", "troy.py"])

# Run the background script in a separate thread to avoid blocking
threading.Thread(target=run_troy_script, daemon=True).start()

# Streamlit frontend content
st.title("Demo App")
st.write("This is a demo text shown on the desktop UI.")
