import os
import sys
import os.path
import httplib2
import time
import signal

if len(sys.argv) != 4 and len(sys.argv) != 6:
    print("""
Sample script to recursively import in Orthanc all the DICOM files
that are stored in some path. Please make sure that Orthanc is running
before starting this script. The files are uploaded through the REST
API.

Usage: %s [hostname] [HTTP port] [path]
Usage: %s [hostname] [HTTP port] [path] [username] [password]
For instance: %s localhost 8042 .
""" % (sys.argv[0], sys.argv[0], sys.argv[0]))
    exit(-1)

URL = 'http://%s:%d/instances' % (sys.argv[1], int(sys.argv[2]))

success = 0
total_size = 0
total_start_time = time.time()
stop_requested = False


def handle_signal(signum, frame):
    """Обрабатывает SIGINT/SIGTERM и просит загрузчик остановиться после текущего файла."""
    global stop_requested
    stop_requested = True
    print("\nShutdown requested, finishing current file and stopping...")


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


def format_size(size_bytes):
    """Convert bytes to human-readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} TB"


# Upload single file
def UploadFile(path):
    """Отправляет один DICOM-файл в Orthanc через REST API."""
    global success
    global total_size
    global stop_requested

    if stop_requested:
        return

    file_size = os.path.getsize(path)
    total_size += file_size

    with open(path, "rb") as f:
        content = f.read()

    try:
        print(f"\nImporting: {path}")
        print(f"File size: {format_size(file_size)}")

        start_time = time.time()

        h = httplib2.Http()
        headers = {'content-type': 'application/dicom'}

        if len(sys.argv) == 6:
            username = sys.argv[4]
            password = sys.argv[5]
            h.add_credentials(username, password)

        resp, response_content = h.request(
            URL,
            'POST',
            body=content,
            headers=headers
        )

        end_time = time.time()
        duration = end_time - start_time

        if resp.status == 200:
            print(f"Status: SUCCESS")
            print(f"Upload time: {duration:.3f} sec")
            print(f"Speed: {format_size(file_size / duration)}/sec")
            success += 1
        else:
            print(f"Status: FAILURE (Is it a DICOM file?)")

    except Exception as e:
        print("Status: ERROR")
        print("Reason:", str(e))
        stop_requested = True


if os.path.isfile(sys.argv[3]):
    UploadFile(sys.argv[3])
else:
    for root, dirs, files in os.walk(sys.argv[3]):
        for f in files:
            UploadFile(os.path.join(root, f))
            if stop_requested:
                break
        if stop_requested:
            break

total_end_time = time.time()
total_duration = total_end_time - total_start_time

print("\n========== FINAL SUMMARY ==========")
print(f"Files successfully imported: {success}")
print(f"Total data transferred: {format_size(total_size)}")
print(f"Total time: {total_duration:.3f} sec")

if total_duration > 0:
    print(f"Average speed: {format_size(total_size / total_duration)}/sec")
