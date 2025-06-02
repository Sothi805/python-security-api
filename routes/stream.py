from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse
import os
from pathlib import Path
from utils.ffmpeg import FFmpegManager
from utils.cleanup import RecordingCleanup
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["streaming"])

# Global instances - will be initialized in main.py
ffmpeg_manager: FFmpegManager = None
cleanup_manager: RecordingCleanup = None
camera_ids: list = []
rtsp_config: dict = {}

def get_base_url():
    """Get the base URL for HLS streaming."""
    # Check if we're running behind a tunnel (Cloudflare, ngrok, etc.)
    tunnel_url = os.getenv("TUNNEL_URL")
    if tunnel_url:
        # Remove trailing slash if present
        return tunnel_url.rstrip('/')
    
    # Fallback to local development URLs
    host = os.getenv("HOST", "localhost")
    port = os.getenv("PORT", "8000")
    # Handle both localhost and 0.0.0.0
    if host == "0.0.0.0":
        host = "localhost"
    return f"http://{host}:{port}"

def init_stream_routes(ffmpeg_mgr: FFmpegManager, cleanup_mgr: RecordingCleanup, cam_ids: list, rtsp_cfg: dict):
    """Initialize the stream routes with required managers and config."""
    global ffmpeg_manager, cleanup_manager, camera_ids, rtsp_config
    ffmpeg_manager = ffmpeg_mgr
    cleanup_manager = cleanup_mgr
    camera_ids = cam_ids
    rtsp_config = rtsp_cfg

@router.post("/stream/live/{camera_id}")
async def start_live_stream(camera_id: str):
    """Start live HLS streaming for a camera."""
    if not ffmpeg_manager:
        raise HTTPException(status_code=500, detail="Stream manager not initialized")
    
    if camera_id not in camera_ids:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    
    # Check if stream is already active
    if ffmpeg_manager.is_stream_active(camera_id, "live"):
        base_url = get_base_url()
        return {"status": "already_active", "camera_id": camera_id, "stream_url": f"{base_url}/hls/{camera_id}/live/index.m3u8"}
    
    # Generate RTSP URL
    rtsp_url = ffmpeg_manager.get_rtsp_url(
        camera_id, 
        rtsp_config["username"], 
        rtsp_config["password"], 
        rtsp_config["ip"]
    )
    
    # Start live stream
    success = await ffmpeg_manager.start_live_stream(camera_id, rtsp_url)
    
    if success:
        base_url = get_base_url()
        return {"status": "started", "camera_id": camera_id, "stream_url": f"{base_url}/hls/{camera_id}/live/index.m3u8"}
    else:
        raise HTTPException(status_code=500, detail="Failed to start live stream")

@router.post("/stream/record/{camera_id}")
async def start_recording(camera_id: str):
    """Start recording for a camera."""
    if not ffmpeg_manager:
        raise HTTPException(status_code=500, detail="Stream manager not initialized")
    
    if camera_id not in camera_ids:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    
    # Check if recording is already active
    if ffmpeg_manager.is_stream_active(camera_id, "record"):
        return {"status": "already_recording", "camera_id": camera_id}
    
    # Generate RTSP URL
    rtsp_url = ffmpeg_manager.get_rtsp_url(
        camera_id, 
        rtsp_config["username"], 
        rtsp_config["password"], 
        rtsp_config["ip"]
    )
    
    # Start recording
    success = await ffmpeg_manager.start_recording(camera_id, rtsp_url)
    
    if success:
        return {"status": "recording_started", "camera_id": camera_id}
    else:
        raise HTTPException(status_code=500, detail="Failed to start recording")

@router.delete("/stream/live/{camera_id}")
async def stop_live_stream(camera_id: str):
    """Stop live streaming for a camera."""
    if not ffmpeg_manager:
        raise HTTPException(status_code=500, detail="Stream manager not initialized")
    
    if camera_id not in camera_ids:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    
    success = ffmpeg_manager.stop_stream(camera_id, "live")
    
    if success:
        return {"status": "stopped", "camera_id": camera_id}
    else:
        return {"status": "not_active", "camera_id": camera_id}

@router.delete("/stream/record/{camera_id}")
async def stop_recording(camera_id: str):
    """Stop recording for a camera."""
    if not ffmpeg_manager:
        raise HTTPException(status_code=500, detail="Stream manager not initialized")
    
    if camera_id not in camera_ids:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    
    success = ffmpeg_manager.stop_stream(camera_id, "record")
    
    if success:
        return {"status": "recording_stopped", "camera_id": camera_id}
    else:
        return {"status": "not_recording", "camera_id": camera_id}

@router.get("/recordings/{camera_id}/{date}/index.m3u8")
async def get_recording_playlist(camera_id: str, date: str):
    """Get M3U8 playlist for recorded segments of a specific date."""
    if not ffmpeg_manager:
        raise HTTPException(status_code=500, detail="Stream manager not initialized")
    
    if camera_id not in camera_ids:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    
    # Validate date format (YYYY-MM-DD)
    try:
        from datetime import datetime
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    
    # Generate playlist
    playlist_content = ffmpeg_manager.generate_recording_playlist(camera_id, date)
    
    if playlist_content is None:
        raise HTTPException(status_code=404, detail=f"No recordings found for camera {camera_id} on {date}")
    
    return Response(
        content=playlist_content,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-cache"}
    )

@router.get("/recordings/{camera_id}/{date}/{hour}/index.m3u8")
async def get_recording_playlist_by_hour(camera_id: str, date: str, hour: str):
    """Get M3U8 playlist for recorded segments of a specific date and hour."""
    if not ffmpeg_manager:
        raise HTTPException(status_code=500, detail="Stream manager not initialized")
    
    if camera_id not in camera_ids:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    
    # Validate date format (YYYY-MM-DD)
    try:
        from datetime import datetime
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    
    # Validate hour format (00-23)
    try:
        hour_int = int(hour)
        if hour_int < 0 or hour_int > 23:
            raise ValueError("Hour must be between 00 and 23")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid hour format. Use 00-23")
    
    # Generate playlist for specific hour
    playlist_content = ffmpeg_manager.generate_recording_playlist_by_hour(camera_id, date, hour)
    
    if playlist_content is None:
        raise HTTPException(status_code=404, detail=f"No recordings found for camera {camera_id} on {date} at hour {hour}")
    
    return Response(
        content=playlist_content,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-cache"}
    )

@router.get("/stream/live/{camera_id}/index.m3u8")
async def get_live_stream_playlist(camera_id: str):
    """Get M3U8 playlist for live streaming with absolute URLs."""
    if not ffmpeg_manager:
        raise HTTPException(status_code=500, detail="Stream manager not initialized")
    
    if camera_id not in camera_ids:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    
    # Check if live stream is active
    if not ffmpeg_manager.is_stream_active(camera_id, "live"):
        raise HTTPException(status_code=404, detail=f"Live stream not active for camera {camera_id}")
    
    # Get the original playlist content from FFmpeg
    playlist_content = ffmpeg_manager.get_live_stream_playlist_with_absolute_urls(camera_id)
    
    if playlist_content is None:
        raise HTTPException(status_code=404, detail=f"Live stream playlist not found for camera {camera_id}")
    
    return Response(
        content=playlist_content,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-cache"}
    )

@router.get("/stream/status/{camera_id}")
async def get_stream_status(camera_id: str):
    """Get current status of streams for a camera."""
    if not ffmpeg_manager:
        raise HTTPException(status_code=500, detail="Stream manager not initialized")
    
    if camera_id not in camera_ids:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    
    live_active = ffmpeg_manager.is_stream_active(camera_id, "live")
    record_active = ffmpeg_manager.is_stream_active(camera_id, "record")
    
    base_url = get_base_url()
    
    return {
        "camera_id": camera_id,
        "live_stream_active": live_active,
        "recording_active": record_active,
        "live_stream_url": f"{base_url}/api/stream/live/{camera_id}/index.m3u8" if live_active else None
    }

@router.get("/stream/status")
async def get_all_streams_status():
    """Get current status of all cameras."""
    if not ffmpeg_manager:
        raise HTTPException(status_code=500, detail="Stream manager not initialized")
    
    statuses = []
    base_url = get_base_url()
    
    for camera_id in camera_ids:
        live_active = ffmpeg_manager.is_stream_active(camera_id, "live")
        record_active = ffmpeg_manager.is_stream_active(camera_id, "record")
        
        statuses.append({
            "camera_id": camera_id,
            "live_stream_active": live_active,
            "recording_active": record_active,
            "live_stream_url": f"{base_url}/api/stream/live/{camera_id}/index.m3u8" if live_active else None
        })
    
    return {"cameras": statuses}

@router.get("/recordings/{camera_id}/dates")
async def get_available_dates(camera_id: str):
    """Get list of available recording dates for a camera."""
    if not cleanup_manager:
        raise HTTPException(status_code=500, detail="Cleanup manager not initialized")
    
    if camera_id not in camera_ids:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    
    dates = cleanup_manager.list_available_dates(camera_id)
    
    return {
        "camera_id": camera_id,
        "available_dates": dates,
        "count": len(dates)
    }

@router.get("/recordings/{camera_id}/{date}/hours")
async def get_available_hours(camera_id: str, date: str):
    """Get list of available recording hours for a specific camera and date."""
    if not cleanup_manager:
        raise HTTPException(status_code=500, detail="Cleanup manager not initialized")
    
    if camera_id not in camera_ids:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    
    # Validate date format (YYYY-MM-DD)
    try:
        from datetime import datetime
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    
    hours = cleanup_manager.list_available_hours(camera_id, date)
    
    return {
        "camera_id": camera_id,
        "date": date,
        "available_hours": hours,
        "count": len(hours)
    }

@router.get("/recordings/size")
async def get_recordings_size():
    """Get disk usage information for all recordings."""
    if not cleanup_manager:
        raise HTTPException(status_code=500, detail="Cleanup manager not initialized")
    
    size_info = cleanup_manager.get_recording_size()
    
    return size_info

@router.post("/recordings/cleanup")
async def trigger_cleanup():
    """Manually trigger cleanup of old recordings."""
    if not cleanup_manager:
        raise HTTPException(status_code=500, detail="Cleanup manager not initialized")
    
    deleted_count = cleanup_manager.cleanup_old_recordings()
    
    return {
        "status": "cleanup_completed",
        "deleted_folders": deleted_count
    }

# NEW ENDPOINTS FOR FLUTTER APP COMPATIBILITY
@router.get("/cameras/status/{camera_id}")
async def get_camera_status(camera_id: str):
    """Get current status of a camera (Flutter-compatible endpoint)."""
    if not ffmpeg_manager:
        raise HTTPException(status_code=500, detail="Stream manager not initialized")
    
    if camera_id not in camera_ids:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    
    live_active = ffmpeg_manager.is_stream_active(camera_id, "live")
    record_active = ffmpeg_manager.is_stream_active(camera_id, "record")
    
    base_url = get_base_url()
    
    return {
        "camera_id": camera_id,
        "live_stream_active": live_active,
        "recording_active": record_active,
        "live_stream_url": f"{base_url}/api/cameras/{camera_id}/live/index.m3u8" if live_active else None
    }

@router.get("/cameras/status")
async def get_all_cameras_status():
    """Get current status of all cameras (Flutter-compatible endpoint)."""
    if not ffmpeg_manager:
        raise HTTPException(status_code=500, detail="Stream manager not initialized")
    
    statuses = []
    base_url = get_base_url()
    
    for camera_id in camera_ids:
        live_active = ffmpeg_manager.is_stream_active(camera_id, "live")
        record_active = ffmpeg_manager.is_stream_active(camera_id, "record")
        
        statuses.append({
            "camera_id": camera_id,
            "live_stream_active": live_active,
            "recording_active": record_active,
            "live_stream_url": f"{base_url}/api/cameras/{camera_id}/live/index.m3u8" if live_active else None
        })
    
    return {"cameras": statuses}

# FLUTTER-COMPATIBLE STREAMING ENDPOINTS
@router.post("/cameras/{camera_id}/live/start")
async def start_camera_live_stream(camera_id: str):
    """Start live HLS streaming for a camera (Flutter-compatible endpoint)."""
    if not ffmpeg_manager:
        raise HTTPException(status_code=500, detail="Stream manager not initialized")
    
    if camera_id not in camera_ids:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    
    # Check if stream is already active
    if ffmpeg_manager.is_stream_active(camera_id, "live"):
        base_url = get_base_url()
        return {"status": "already_active", "camera_id": camera_id, "stream_url": f"{base_url}/api/cameras/{camera_id}/live/index.m3u8"}
    
    # Generate RTSP URL
    rtsp_url = ffmpeg_manager.get_rtsp_url(
        camera_id, 
        rtsp_config["username"], 
        rtsp_config["password"], 
        rtsp_config["ip"]
    )
    
    # Start live stream
    success = await ffmpeg_manager.start_live_stream(camera_id, rtsp_url)
    
    if success:
        base_url = get_base_url()
        return {"status": "started", "camera_id": camera_id, "stream_url": f"{base_url}/api/cameras/{camera_id}/live/index.m3u8"}
    else:
        raise HTTPException(status_code=500, detail="Failed to start live stream")

@router.post("/cameras/{camera_id}/live/stop")
async def stop_camera_live_stream(camera_id: str):
    """Stop live streaming for a camera (Flutter-compatible endpoint)."""
    if not ffmpeg_manager:
        raise HTTPException(status_code=500, detail="Stream manager not initialized")
    
    if camera_id not in camera_ids:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    
    success = ffmpeg_manager.stop_stream(camera_id, "live")
    
    if success:
        return {"status": "stopped", "camera_id": camera_id}
    else:
        return {"status": "not_active", "camera_id": camera_id}

@router.get("/cameras/{camera_id}/live/index.m3u8")
async def get_camera_live_stream_playlist(camera_id: str):
    """Get M3U8 playlist for live streaming with absolute URLs (Flutter-compatible endpoint)."""
    if not ffmpeg_manager:
        raise HTTPException(status_code=500, detail="Stream manager not initialized")
    
    if camera_id not in camera_ids:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    
    # Check if live stream is active
    if not ffmpeg_manager.is_stream_active(camera_id, "live"):
        raise HTTPException(status_code=404, detail=f"Live stream not active for camera {camera_id}")
    
    # Get the original playlist content from FFmpeg
    playlist_content = ffmpeg_manager.get_live_stream_playlist_with_absolute_urls(camera_id)
    
    if playlist_content is None:
        raise HTTPException(status_code=404, detail=f"Live stream playlist not found for camera {camera_id}")
    
    return Response(
        content=playlist_content,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-cache"}
    ) 