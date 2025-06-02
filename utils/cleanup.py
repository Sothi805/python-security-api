import os
import shutil
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

class RecordingCleanup:
    def __init__(self, hls_root: str, retention_days: int):
        self.hls_root = Path(hls_root)
        self.retention_days = retention_days
        
    def cleanup_old_recordings(self) -> int:
        """Clean up recordings older than retention period. Returns number of folders deleted."""
        try:
            cutoff_date = datetime.now() - timedelta(days=self.retention_days)
            cutoff_date_str = cutoff_date.strftime("%Y-%m-%d")
            
            deleted_count = 0
            
            # Scan each camera's recordings
            for camera_dir in self.hls_root.iterdir():
                if camera_dir.is_dir():
                    recordings_dir = camera_dir / "recordings"
                    
                    if recordings_dir.exists():
                        deleted_count += self._cleanup_camera_recordings(recordings_dir, cutoff_date_str)
            
            logger.info(f"Cleanup completed. Deleted {deleted_count} old recording folders.")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Failed to cleanup recordings: {e}")
            return 0
    
    def _cleanup_camera_recordings(self, recordings_dir: Path, cutoff_date_str: str) -> int:
        """Clean up recordings for a specific camera."""
        deleted_count = 0
        
        try:
            for date_folder in recordings_dir.iterdir():
                if date_folder.is_dir():
                    folder_name = date_folder.name
                    
                    # Check if folder name is a valid date and is older than cutoff
                    if self._is_valid_date_folder(folder_name) and folder_name < cutoff_date_str:
                        try:
                            shutil.rmtree(date_folder)
                            logger.info(f"Deleted old recording folder: {date_folder}")
                            deleted_count += 1
                        except Exception as e:
                            logger.error(f"Failed to delete folder {date_folder}: {e}")
            
        except Exception as e:
            logger.error(f"Failed to cleanup camera recordings in {recordings_dir}: {e}")
        
        return deleted_count
    
    def _is_valid_date_folder(self, folder_name: str) -> bool:
        """Check if folder name is a valid date in YYYY-MM-DD format."""
        try:
            datetime.strptime(folder_name, "%Y-%m-%d")
            return True
        except ValueError:
            return False
    
    def get_recording_size(self, camera_id: str = None) -> dict:
        """Get disk usage information for recordings."""
        try:
            total_size = 0
            camera_sizes = {}
            
            cameras_to_check = []
            
            if camera_id:
                cameras_to_check = [camera_id]
            else:
                # Check all camera directories
                for camera_dir in self.hls_root.iterdir():
                    if camera_dir.is_dir():
                        cameras_to_check.append(camera_dir.name)
            
            for cam_id in cameras_to_check:
                camera_dir = self.hls_root / cam_id / "recordings"
                
                if camera_dir.exists():
                    camera_size = self._get_directory_size(camera_dir)
                    camera_sizes[cam_id] = camera_size
                    total_size += camera_size
                else:
                    camera_sizes[cam_id] = 0
            
            return {
                "total_size_bytes": total_size,
                "total_size_mb": round(total_size / 1024 / 1024, 2),
                "camera_sizes": camera_sizes
            }
            
        except Exception as e:
            logger.error(f"Failed to get recording size: {e}")
            return {"total_size_bytes": 0, "total_size_mb": 0, "camera_sizes": {}}
    
    def _get_directory_size(self, directory: Path) -> int:
        """Calculate total size of directory in bytes."""
        total_size = 0
        
        try:
            for dirpath, dirnames, filenames in os.walk(directory):
                for filename in filenames:
                    filepath = Path(dirpath) / filename
                    try:
                        total_size += filepath.stat().st_size
                    except (OSError, FileNotFoundError):
                        pass  # Skip files that can't be accessed
        except Exception:
            pass
        
        return total_size
    
    def list_available_dates(self, camera_id: str) -> List[str]:
        """List all available recording dates for a camera."""
        try:
            recordings_dir = self.hls_root / camera_id / "recordings"
            
            if not recordings_dir.exists():
                return []
            
            dates = []
            for date_folder in recordings_dir.iterdir():
                if date_folder.is_dir() and self._is_valid_date_folder(date_folder.name):
                    dates.append(date_folder.name)
            
            return sorted(dates, reverse=True)  # Most recent first
            
        except Exception as e:
            logger.error(f"Failed to list dates for camera {camera_id}: {e}")
            return []

    def list_available_hours(self, camera_id: str, date: str) -> List[str]:
        """List all available recording hours for a camera and specific date."""
        try:
            date_dir = self.hls_root / camera_id / "recordings" / date
            
            if not date_dir.exists():
                return []
            
            hours = []
            for hour_folder in date_dir.iterdir():
                if hour_folder.is_dir() and hour_folder.name.isdigit():
                    # Check if hour folder has any recording files
                    if any(hour_folder.glob("*.ts")):
                        hours.append(hour_folder.name.zfill(2))  # Ensure 2-digit format (01, 02, etc.)
            
            return sorted(hours)  # Sort chronologically
            
        except Exception as e:
            logger.error(f"Failed to list hours for camera {camera_id}, date {date}: {e}")
            return [] 