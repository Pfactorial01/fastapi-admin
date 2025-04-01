import os
import sys

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import and run the main application
from examples.main import app_

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app_, host="0.0.0.0", port=8080) 