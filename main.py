import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import schedule
import threading
import time
from pathlib import Path

from routes.stream import router as stream_router, init_stream_routes
from utils.ffmpeg import FFmpegManager
from utils.cleanup import RecordingCleanup

# Load environment variables
load_dotenv()

# Configure logging with DEBUG level
logging.basicConfig(
    level=logging.DEBUG,  # Changed to DEBUG to see FFmpeg output
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global managers
ffmpeg_manager: FFmpegManager = None
cleanup_manager: RecordingCleanup = None

def cleanup_job():
    """Background job for cleaning up old recordings."""
    if cleanup_manager:
        logger.info("Running scheduled cleanup...")
        cleanup_manager.cleanup_old_recordings()

def run_scheduler():
    """Run the scheduler in a separate thread."""
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application startup and shutdown."""
    global ffmpeg_manager, cleanup_manager
    
    # Startup
    logger.info("Starting CCTV HLS Streaming System...")
    
    # Get configuration from environment
    rtsp_username = os.getenv("RTSP_USERNAME", "admin")
    rtsp_password = os.getenv("RTSP_PASSWORD", "password")
    rtsp_ip = os.getenv("RTSP_IP", "192.168.1.100")
    camera_ids_str = os.getenv("CAMERA_IDS", "101,102")
    hls_root = os.getenv("HLS_ROOT", "./hls")
    retention_days = int(os.getenv("RECORDING_RETENTION_DAYS", "1"))
    
    # Parse camera IDs
    camera_ids = [id.strip() for id in camera_ids_str.split(",")]
    
    # Normalize camera IDs: remove 'camera_' prefix if present for RTSP URL generation
    normalized_camera_ids = []
    for camera_id in camera_ids:
        if camera_id.startswith('camera_'):
            normalized_id = camera_id.replace('camera_', '')
            normalized_camera_ids.append(normalized_id)
            logger.info(f"Normalized camera ID: {camera_id} -> {normalized_id}")
        else:
            normalized_camera_ids.append(camera_id)
    
    logger.info(f"Original camera IDs: {camera_ids}")
    logger.info(f"Normalized camera IDs for RTSP: {normalized_camera_ids}")
    
    # Create HLS root directory
    hls_path = Path(hls_root)
    hls_path.mkdir(exist_ok=True)
    
    # Initialize managers
    ffmpeg_manager = FFmpegManager(hls_root)
    cleanup_manager = RecordingCleanup(hls_root, retention_days)
    
    # Initialize stream routes
    rtsp_config = {
        "username": rtsp_username,
        "password": rtsp_password,
        "ip": rtsp_ip
    }
    init_stream_routes(ffmpeg_manager, cleanup_manager, normalized_camera_ids, rtsp_config)
    
    # Schedule daily cleanup at 2 AM
    schedule.every().day.at("02:00").do(cleanup_job)
    
    # Start scheduler thread
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    
    # Run initial cleanup
    cleanup_manager.cleanup_old_recordings()
    
    logger.info(f"System initialized with cameras: {normalized_camera_ids}")
    logger.info(f"HLS root: {hls_path.absolute()}")
    logger.info(f"Recording retention: {retention_days} days")
    
    # Auto-start recording for all cameras (optional)
    auto_start_recording = os.getenv("AUTO_START_RECORDING", "true").lower() == "true"
    if auto_start_recording:
        logger.info("Auto-starting recording for all cameras...")
        for camera_id in normalized_camera_ids:
            try:
                rtsp_url = ffmpeg_manager.get_rtsp_url(camera_id, rtsp_username, rtsp_password, rtsp_ip)
                logger.info(f"Testing RTSP URL for camera {camera_id}: {rtsp_url}")
                await ffmpeg_manager.start_recording(camera_id, rtsp_url)
                logger.info(f"Started auto-recording for camera {camera_id}")
            except Exception as e:
                logger.error(f"Failed to start auto-recording for camera {camera_id}: {e}")
    
    yield
    
    # Shutdown
    logger.info("Shutting down CCTV system...")
    if ffmpeg_manager:
        ffmpeg_manager.stop_all_streams()
    logger.info("System shutdown complete.")

# Create FastAPI app
app = FastAPI(
    title="CCTV HLS Streaming System",
    description="Real-time RTSP to HLS streaming with recording capabilities",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify actual frontend domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routes
app.include_router(stream_router)

# Serve HLS files - FIXED: Use the correct path where files are actually generated
hls_root = os.getenv("HLS_ROOT", "./hls")
hls_absolute_path = Path(hls_root).resolve()

# Ensure the directory exists
hls_absolute_path.mkdir(exist_ok=True)

logger.info(f"Serving HLS files from: {hls_absolute_path}")
app.mount("/hls", StaticFiles(directory=str(hls_absolute_path)), name="hls")

# Health check endpoint
@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "message": "CCTV HLS Streaming System",
        "status": "running",
        "version": "1.0.0"
    }

@app.get("/api/health")
async def api_health_check():
    """Detailed health check for Flutter app."""
    camera_ids_str = os.getenv("CAMERA_IDS", "101,102")
    camera_ids = [id.strip() for id in camera_ids_str.split(",")]
    
    health_status = {
        "status": "healthy",
        "timestamp": time.time(),
        "cameras_configured": camera_ids,  # Return original IDs for Flutter compatibility
        "hls_root": str(Path(os.getenv("HLS_ROOT", "./hls")).resolve()),
        "retention_days": int(os.getenv("RECORDING_RETENTION_DAYS", "1"))
    }
    
    # Check if managers are initialized
    if ffmpeg_manager:
        health_status["ffmpeg_manager"] = "initialized"
        # Add process status for each camera using normalized IDs
        process_status = {}
        for camera_id in camera_ids:
            # Use normalized ID for process checking
            normalized_id = camera_id.replace('camera_', '') if camera_id.startswith('camera_') else camera_id
            process_status[camera_id] = {  # But report with original ID
                "live": ffmpeg_manager.get_process_status(normalized_id, "live"),
                "record": ffmpeg_manager.get_process_status(normalized_id, "record")
            }
        health_status["process_status"] = process_status
    else:
        health_status["ffmpeg_manager"] = "not_initialized"
        health_status["status"] = "degraded"
    
    if cleanup_manager:
        health_status["cleanup_manager"] = "initialized"
    else:
        health_status["cleanup_manager"] = "not_initialized"
        health_status["status"] = "degraded"
    
    return health_status

@app.get("/health")
async def health_check():
    """Legacy health check endpoint."""
    return await api_health_check()

@app.post("/api/cleanup")
async def api_cleanup():
    """Manually trigger cleanup of old recordings (Flutter-compatible endpoint)."""
    if not cleanup_manager:
        raise HTTPException(status_code=500, detail="Cleanup manager not initialized")
    
    deleted_count = cleanup_manager.cleanup_old_recordings()
    
    return {
        "message": "Cleanup completed",
        "deleted_folders": deleted_count
    }

if __name__ == "__main__":
    import uvicorn
    
    # Get host and port from environment
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=True,
        log_level="info"
    ) 