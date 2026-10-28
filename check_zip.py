
import zipfile
from pathlib import Path

zip_path = Path.home() / "Downloads" / "Prem.zip"

if not zip_path.exists():
    print("ZIP not found:", zip_path)
else:
    with zipfile.ZipFile(zip_path) as archive:
        for item in archive.infolist():
            if not item.is_dir():
                print(item.filename, "|", item.file_size, "bytes")
