import sys
import os

# Add root folder to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from mangum import Mangum

# Wrap app for Vercel serverless functions
handler = Mangum(app)