import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app
from mangum import Mangum

# Vercel serverless entry point using Mangum ASGI handler
handler = Mangum(app)