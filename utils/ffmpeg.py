import asyncio
import subprocess
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
import threading
import schedule
import time

logger = logging.getLogger(__name__)

class FFmpegManager:
    def __init__(self, hls_root: str):
        self.hls_root = Path(hls_root)
        self.processes: Dict[str, subprocess.Popen] = {}
        self.recording_cameras = set()  # Track which cameras should be recording
        
        # Start the hourly rotation scheduler
        self._start_hourly_scheduler()
        
    def _start_hourly_scheduler(self):
        """Start background thread for hourly recording rotation."""
        def run_scheduler():
            # Schedule hourly recording rotation at minute 0 of every hour
            schedule.every().hour.at(":00").do(self._rotate_hourly_recordings)
            
            while True:
                schedule.run_pending()
                time.sleep(30)  # Check every 30 seconds
        
        scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
        scheduler_thread.start()
        logger.info("Started hourly recording rotation scheduler")
    
    def _rotate_hourly_recordings(self):
        """Restart all active recordings to create new hour directories."""
        logger.info("Rotating hourly recordings...")
        
        # Get list of cameras that need recording rotation
        cameras_to_rotate = []
        for process_key in list(self.processes.keys()):
            if process_key.startswith("record_"):
                camera_id = process_key.replace("record_", "")
                cameras_to_rotate.append(camera_id)
        
        # Restart each recording
        for camera_id in cameras_to_rotate:
            if camera_id in self.recording_cameras:
                logger.info(f"Rotating recording for camera {camera_id}")
                
                # Get the RTSP URL for this camera
                rtsp_url = self._get_camera_rtsp_url(camera_id)
                if rtsp_url:
                    # Stop current recording
                    self.stop_stream(camera_id, "record")
                    
                    # Wait a moment for cleanup
                    time.sleep(2)
                    
                    # Start new recording with new hour directory
                    asyncio.create_task(self.start_recording(camera_id, rtsp_url))
    
    def _get_camera_rtsp_url(self, camera_id: str) -> Optional[str]:
        """Get RTSP URL for a camera (you may need to adjust this based on your config)."""
        # DVR configuration - all cameras/channels use same IP
        dvr_config = {
            "username": "admin", 
            "password": "iME101112", 
            "ip": "192.168.100.100"
        }
        
        # Camera ID represents the channel number on the DVR
        return self.get_rtsp_url(camera_id, dvr_config["username"], dvr_config["password"], dvr_config["ip"])

    def get_rtsp_url(self, camera_id: str, username: str, password: str, ip: str) -> str:
        """Generate RTSP URL for the camera channel on the DVR."""
        # For DVR systems, camera_id represents the channel number
        # Use the provided DVR IP and append the channel to the RTSP path
        rtsp_url = f"rtsp://{username}:{password}@{ip}:554/Streaming/Channels/{camera_id}"
        logger.info(f"Generated RTSP URL for camera/channel {camera_id}: {rtsp_url}")
        return rtsp_url
    
    def _log_ffmpeg_output(self, process: subprocess.Popen, camera_id: str, stream_type: str):
        """Log FFmpeg output in a separate thread."""
        def log_output():
            try:
                while True:
                    output = process.stderr.readline()
                    if output == '' and process.poll() is not None:
                        break
                    if output:
                        logger.debug(f"FFmpeg {stream_type} {camera_id}: {output.strip()}")
            except Exception as e:
                logger.error(f"Error logging FFmpeg output for {camera_id}: {e}")

        thread = threading.Thread(target=log_output, daemon=True)
        thread.start()
    
    async def start_live_stream(self, camera_id: str, rtsp_url: str) -> bool:
        """Start live stream for a camera."""
        try:
            # Create live stream directory
            live_dir = self.hls_root / camera_id / "live"
            live_dir.mkdir(parents=True, exist_ok=True)
            
            # Output file for live stream
            output_path = live_dir / "index.m3u8"
            
            # FFmpeg command for live streaming
            cmd = [
                "ffmpeg",
                "-rtsp_transport", "tcp",
                "-i", rtsp_url,
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-tune", "zerolatency",
                "-g", "30",
                "-c:a", "aac",
                "-b:a", "128k",
                "-f", "hls",
                "-hls_time", "4",
                "-hls_list_size", "10",
                "-hls_flags", "delete_segments+append_list",
                "-hls_segment_filename", str(live_dir / "segment_%03d.ts"),
                "-y",
                str(output_path)
            ]
            
            logger.info(f"Starting live stream for camera {camera_id}")
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                cwd=str(self.hls_root.parent)
            )
            
            # Start logging FFmpeg output
            self._log_ffmpeg_output(process, camera_id, "live")
            
            self.processes[f"live_{camera_id}"] = process
            logger.info(f"Started live stream for camera {camera_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start live stream for camera {camera_id}: {e}")
            return False
    
    async def start_recording(self, camera_id: str, rtsp_url: str) -> bool:
        """Start recording for a camera with current hour directory."""
        try:
            # Add to recording cameras tracking
            self.recording_cameras.add(camera_id)
            
            now = datetime.now()
            recording_base = self.hls_root / camera_id / "recordings"
            
            # Create directory structure for CURRENT hour: YYYY-MM-DD/HH/
            date_dir = recording_base / now.strftime("%Y-%m-%d") / now.strftime("%H")
            date_dir.mkdir(parents=True, exist_ok=True)
            
            # Use proper segment filename with strftime for current time
            segment_filename = str(date_dir / "%Y%m%d_%H%M%S.ts")
            
            # FFmpeg command for recording with proper segmentation
            cmd = [
                "ffmpeg",
                "-rtsp_transport", "tcp",  # Use TCP for better reliability
                "-i", rtsp_url,
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "23",  # Quality setting
                "-c:a", "aac",
                "-b:a", "128k",
                "-f", "segment",
                "-segment_time", "60",  # 1 minute segments
                "-segment_format", "mpegts",
                "-segment_list_flags", "+live",
                "-segment_list_size", "0",  # Keep all segments
                "-strftime", "1",  # Enable strftime for filename
                "-reset_timestamps", "1",  # Reset timestamps for each segment
                "-y",  # Overwrite output files
                segment_filename
            ]
            
            logger.info(f"Starting recording for camera {camera_id} in hour {now.strftime('%H')} with command: {' '.join(cmd)}")
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                cwd=str(self.hls_root.parent)  # Set working directory
            )
            
            # Start logging FFmpeg output
            self._log_ffmpeg_output(process, camera_id, "record")
            
            self.processes[f"record_{camera_id}"] = process
            logger.info(f"Started recording for camera {camera_id} in directory: {date_dir}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start recording for camera {camera_id}: {e}")
            return False
    
    def stop_stream(self, camera_id: str, stream_type: str = "live") -> bool:
        """Stop a stream (live or record) for a camera."""
        process_key = f"{stream_type}_{camera_id}"
        
        if process_key in self.processes:
            try:
                process = self.processes[process_key]
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning(f"Process {process_key} didn't terminate gracefully, killing it")
                    process.kill()
                    process.wait()
                
                del self.processes[process_key]
                
                # Remove from recording cameras tracking if it's a recording stream
                if stream_type == "record":
                    self.recording_cameras.discard(camera_id)
                
                logger.info(f"Stopped {stream_type} stream for camera {camera_id}")
                return True
            except Exception as e:
                logger.error(f"Failed to stop {stream_type} stream for camera {camera_id}: {e}")
                return False
        
        return False
    
    def stop_all_streams(self):
        """Stop all active streams."""
        for process_key in list(self.processes.keys()):
            try:
                process = self.processes[process_key]
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            except Exception as e:
                logger.error(f"Failed to stop process {process_key}: {e}")
        
        self.processes.clear()
        logger.info("Stopped all streams")
    
    def is_stream_active(self, camera_id: str, stream_type: str = "live") -> bool:
        """Check if a stream is currently active."""
        process_key = f"{stream_type}_{camera_id}"
        
        if process_key in self.processes:
            process = self.processes[process_key]
            if process.poll() is None:
                return True
            else:
                # Process has terminated, remove it from our tracking
                logger.warning(f"Process {process_key} has terminated unexpectedly")
                del self.processes[process_key]
                return False
        
        return False
    
    def get_process_status(self, camera_id: str, stream_type: str = "live") -> dict:
        """Get detailed status of a process."""
        process_key = f"{stream_type}_{camera_id}"
        
        if process_key in self.processes:
            process = self.processes[process_key]
            return {
                "running": process.poll() is None,
                "pid": process.pid,
                "returncode": process.returncode
            }
        
        return {"running": False, "pid": None, "returncode": None}
    
    def generate_recording_playlist(self, camera_id: str, date: str) -> Optional[str]:
        """Generate M3U8 playlist for recorded segments of a specific date."""
        try:
            import os
            
            recording_dir = self.hls_root / camera_id / "recordings" / date
            
            if not recording_dir.exists():
                return None
            
            segments = []
            
            # Get base URL for absolute URLs
            host = os.getenv("HOST", "localhost")
            port = os.getenv("PORT", "8000")
            if host == "0.0.0.0":
                host = "localhost"
            base_url = f"http://{host}:{port}"
            
            # Scan all hours for the given date
            for hour_dir in sorted(recording_dir.iterdir()):
                if hour_dir.is_dir() and hour_dir.name.isdigit():
                    # Scan all minute segments in the hour
                    for segment_file in sorted(hour_dir.glob("*.ts")):
                        relative_path = f"{camera_id}/recordings/{date}/{hour_dir.name}/{segment_file.name}"
                        segments.append(f"{base_url}/hls/{relative_path}")
            
            if not segments:
                return None
            
            # Generate M3U8 content
            playlist_content = "#EXTM3U\n"
            playlist_content += "#EXT-X-VERSION:3\n"
            playlist_content += "#EXT-X-TARGETDURATION:60\n"
            playlist_content += "#EXT-X-MEDIA-SEQUENCE:0\n"
            
            for segment in segments:
                playlist_content += "#EXTINF:60.0,\n"
                playlist_content += f"{segment}\n"
            
            playlist_content += "#EXT-X-ENDLIST\n"
            
            return playlist_content
            
        except Exception as e:
            logger.error(f"Failed to generate playlist for camera {camera_id}, date {date}: {e}")
            return None

    def generate_recording_playlist_by_hour(self, camera_id: str, date: str, hour: str) -> Optional[str]:
        """Generate M3U8 playlist for recorded segments of a specific date and hour."""
        try:
            import os
            
            hour_dir = self.hls_root / camera_id / "recordings" / date / hour
            
            if not hour_dir.exists():
                return None
            
            segments = []
            
            # Get base URL for absolute URLs
            host = os.getenv("HOST", "localhost")
            port = os.getenv("PORT", "8000")
            if host == "0.0.0.0":
                host = "localhost"
            base_url = f"http://{host}:{port}"
            
            # Scan all minute segments in the specific hour
            for segment_file in sorted(hour_dir.glob("*.ts")):
                relative_path = f"{camera_id}/recordings/{date}/{hour}/{segment_file.name}"
                segments.append(f"{base_url}/hls/{relative_path}")
            
            if not segments:
                return None
            
            # Generate M3U8 content
            playlist_content = "#EXTM3U\n"
            playlist_content += "#EXT-X-VERSION:3\n"
            playlist_content += "#EXT-X-TARGETDURATION:60\n"
            playlist_content += "#EXT-X-MEDIA-SEQUENCE:0\n"
            
            for segment in segments:
                playlist_content += "#EXTINF:60.0,\n"
                playlist_content += f"{segment}\n"
            
            playlist_content += "#EXT-X-ENDLIST\n"
            
            return playlist_content
            
        except Exception as e:
            logger.error(f"Failed to generate playlist for camera {camera_id}, date {date}, hour {hour}: {e}")
            return None

    def get_live_stream_playlist_with_absolute_urls(self, camera_id: str) -> Optional[str]:
        """Get live stream playlist with absolute URLs for segments."""
        try:
            import os
            
            live_dir = self.hls_root / camera_id / "live"
            playlist_path = live_dir / "index.m3u8"
            
            if not playlist_path.exists():
                return None
            
            # Get base URL for absolute URLs
            host = os.getenv("HOST", "localhost")
            port = os.getenv("PORT", "8000")
            if host == "0.0.0.0":
                host = "localhost"
            base_url = f"http://{host}:{port}"
            
            # Read the original playlist
            with open(playlist_path, 'r') as f:
                content = f.read()
            
            # Convert relative URLs to absolute URLs
            lines = content.strip().split('\n')
            modified_lines = []
            
            for line in lines:
                if line.endswith('.ts'):
                    # Convert relative segment URL to absolute URL
                    absolute_url = f"{base_url}/hls/{camera_id}/live/{line}"
                    modified_lines.append(absolute_url)
                else:
                    # Keep other lines as-is (metadata, etc.)
                    modified_lines.append(line)
            
            return '\n'.join(modified_lines)
            
        except Exception as e:
            logger.error(f"Failed to get live stream playlist for camera {camera_id}: {e}")
            return None 