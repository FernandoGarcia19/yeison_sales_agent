from fastapi import FastAPI
from app.core. config import settings

# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    debug=settings.debug
)

# Basic route
@app. get("/")
async def root():
    return {
        "message": f"Welcome to {settings.app_name}!",
        "version": settings.version,
        "status": "running"
    }

@app.get("/health")
async def health_check():
    return {
        "status":  "healthy",
        "app": settings.app_name,
        "version": settings.version
    }

# Run the app
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=True  # Auto-reload on code changes
    )