import sys
import os

# Add root folder to python path so backend imports work smoothly
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app