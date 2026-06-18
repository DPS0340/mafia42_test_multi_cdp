"""Wrapper to ensure correct working directory before starting server."""
import os, sys
os.chdir(r"C:\Users\admin\mafia42_test")
sys.path.insert(0, ".")
from megaphone.app import main
main()
