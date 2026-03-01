import os
import sys
import django

# Add the project directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'yt_helper.settings')
django.setup()


import json
import requests
import subprocess
from time import time,sleep

api_key = "c4e263b79emshe14e6b3519db2bbp1b4eb8jsn358501767a46"

def get_separate_streams(youtube_video_id, api_key):
    # Using DataFanatic or similar endpoint
    url = "https://ytstream-download-youtube-videos.p.rapidapi.com/dl"

    querystring = {"id":youtube_video_id}

    headers = {
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": "ytstream-download-youtube-videos.p.rapidapi.com"
    }

    try:
        response = requests.get(url, headers=headers, params=querystring)
        data = response.json()
        with open("yt_api_response.json",'w') as f :
            json.dump(data, f)
        
        # 1. Find the best VIDEO-ONLY stream (look for 1080p or 720p)
        video_url = None
        candidates = data.get('formats', []) + data.get('videos', []) + data.get('adaptiveFormats', [])
        
        for item in candidates:
            # We want video, no audio (or ignores audio), highest quality
            if item.get('height') == 720 and item.get('mimeType', '').startswith('video/mp4'):
                video_url = item['url']
                break
        
        # Fallback: take any MP4 video
        if not video_url:
             for item in candidates:
                if item.get('mimeType', '').startswith('video/mp4'):
                    video_url = item['url']
                    break

        # 2. Find the best AUDIO-ONLY stream
        audio_url = None
        for item in candidates:
            if item.get('mimeType', '').startswith('audio/mp4') or item.get('mimeType', '').startswith('audio/webm'):
                audio_url = item['url']
                break
        
        if video_url and audio_url:
            return video_url, audio_url
        else:
            print("Could not find separate video and audio streams.")
            return None, None

    except Exception as e:
        print(f"API Error: {e}")
        return None, None


# --- USAGE ---
# api_key = "YOUR_KEY"
def process_clip(direct_url, start_sec, duration, output_prefix):
    if not direct_url:
        print("Aborting: Invalid URL")
        return

    out_720 = f"{output_prefix}_720p.mp4"
    out_480 = f"{output_prefix}_480p.mp4"

    cmd = [
        'ffmpeg',
        '-y',
        '-ss', str(start_sec),    # Seek on input (Fast)
        '-t', str(duration),      # Duration
        '-i', direct_url,         # Input URL
        
        # Filter Complex: Split video into two chains
        '-filter_complex', 
        '[0:v]split=2[v720][v480];' 
        '[v720]scale=-2:720[out720];'
        '[v480]scale=-2:480[out480]',
        
        # Output 1: 720p
        '-map', '[out720]',
        '-map', '0:a',            # This will now work because we ensured the URL has audio
        '-c:v', 'libx264', '-preset', 'superfast', '-crf', '23',
        '-c:a', 'aac',
        out_720,
        
        # Output 2: 480p
        '-map', '[out480]',
        '-map', '0:a',
        '-c:v', 'libx264', '-preset', 'superfast', '-crf', '28',
        '-c:a', 'aac',
        out_480
    ]

    print(f"Running FFmpeg on {direct_url[:30]}...")
    try:
        subprocess.run(cmd, check=True)
        print("Clipping complete!")
    except subprocess.CalledProcessError as e:
        print(f"FFmpeg failed with exit code {e.returncode}")

def process_dual_input_clip(video_url, audio_url, start_sec, duration, output_prefix="test"):
    """
    Takes separate video and audio URLs and generates 720p and 480p clips.
    """
    out_720 = f"{output_prefix}_720p.mp4"
    out_480 = f"{output_prefix}_480p.mp4"
    print(out_720)
    cmd = [
        'ffmpeg',
        '-y',  # Overwrite existing files
        
        # --- INPUT 0: Video Stream ---
        '-ss', str(start_sec),    # Seek on remote server (Video)
        '-t', str(duration),      # Duration to download
        '-i', video_url,
        
        # --- INPUT 1: Audio Stream ---
        '-ss', str(start_sec),    # Seek on remote server (Audio)
        '-t', str(duration),
        '-i', audio_url,
        
        # --- FILTER COMPLEX ---
        # 1. Split the video stream [0:v] into two copies: [v_in_720] and [v_in_480]
        # 2. Scale [v_in_720] to 720p height (keeping aspect ratio) -> [v_out_720]
        # 3. Scale [v_in_480] to 480p height (keeping aspect ratio) -> [v_out_480]
        '-filter_complex', 
        '[0:v]split=2[v_in_720][v_in_480];'
        '[v_in_720]scale=-2:720[v_out_720];'
        '[v_in_480]scale=-2:480[v_out_480]',
        
        # --- OUTPUT 1: 720p ---
        '-map', '[v_out_720]',    # Use the 720p scaled video
        '-map', '1:a',            # Use the audio from Input 1
        '-c:v', 'libx264',        # Re-encode video
        '-preset', 'medium',   # Fast encoding for DigitalOcean CPU
        '-crf', '18',             # Standard quality
        '-c:a', 'aac',            # Re-encode audio
        out_720,
        
        # --- OUTPUT 2: 480p ---
        '-map', '[v_out_480]',    # Use the 480p scaled video
        '-map', '1:a',            # Use the SAME audio from Input 1
        '-c:v', 'libx264',
        '-preset', 'medium',
        '-crf', '23',             # Slightly lower quality/size for 480p
        '-c:a', 'aac',
        out_480
    ]

    print(f"Running Dual-Input FFmpeg Clip...")
    try:
        # Run command and capture output for debugging if needed
        subprocess.run(cmd, check=True)
        print(f"Success! Created:\n1. {out_720}\n2. {out_480}")
    except subprocess.CalledProcessError as e:
        print(f"FFmpeg Error (Exit Code {e.returncode})")

# vid, aud = get_separate_streams("ID_gMQIktMg", api_key)


# if vid and aud:
#     process_dual_input_clip(vid, aud, 0, 60, "final_test")


t1 = time()
ytUrl = "https://www.youtube.com/watch?v=l30e6vUocyc"
url = "https://youtube-info-download-api.p.rapidapi.com/ajax/download.php"

querystring = {"format":"480","add_info":"0","url":ytUrl,"audio_quality":"128","allow_extended_duration":"false",
"no_merge":"false","audio_language":"en"}

headers = {
	"x-rapidapi-key": api_key,
	"x-rapidapi-host": "youtube-info-download-api.p.rapidapi.com"
}

# response = requests.get(url, headers=headers, params=querystring)
# response = response.json()
# t2 = time() 
# print(f"Time taken to get progress URL: {t2 - t1} seconds")
# downloadUrl = None
# alternateDownloadUrls = None
# if response["success"]:
#     t3 = time()
#     progressUrl = response['progress_url']
#     print(f"Progress URL: {progressUrl}")
#     attempts = 0
#     while not time() - t3 > 60*5: # 5 minutes timeout
#         response = requests.get(progressUrl, headers=headers)
#         response = response.json()
#         attempts += 1
#         if response["success"]:
#             downloadUrl = response['download_url']
#             alternateDownloadUrls = response['alternative_download_urls']
#             print(f"Download URL: {downloadUrl}")
#             print(f"Alternate Download URLs: {alternateDownloadUrls}")
#             break
#         sleep(2)
#     t4 = time()
#     print(f"Time taken to get download URL: {t4 - t3}, attempts: {attempts} seconds")
# if downloadUrl:
#     t5 = time()
#     process_clip(downloadUrl, 60, 240, "final_test_480p")
#     t6 = time()
#     print(f"Time taken to process clip: {t6 - t5} seconds")
# t7 = time()
# print(f"Total time taken: {t7 - t1} seconds")



# downloadUrl = "https://jerome62.savenow.to/pacific/?aU1FTny4j3mB55HjSdQbGnZ"
# t5 = time()
# process_clip(downloadUrl, 60, 240, "final_test_480p")
# t6 = time()
# print(f"Time taken to process clip: {t6 - t5} seconds")

def working():
    import time
    import yt_dlp
    import subprocess

    ytUrl = "https://youtu.be/AXVLDt6-_Sk?si=Fp0kuMIoKsvTY5C2"
    startSec = 10
    endSec = 20
    outPath = "final_high_quality_cut_3.mp4"

    PROXIES = ["http://divyanshusoni061:ZT4SVooGRa@148.113.17.137:11034"]
    print(">>> Step 1: Extracting Stream URLs...")


    ydl_opts_extract = {
        'quiet': True,
        'force_ipv6': True,
    
        # Get Best Video (up to 720p) + Best Audio
        # This separates them to get higher bitrate than the standard 'mp4' file
        'format': 'best[height<=720][protocol^=http]',
        "cookiefile": "cookies_ff.txt",
        'js_runtimes': { 'node': {}},  

        'verbose': True,
        'proxy': PROXIES[0],

    }

    video_url = None
    audio_url = None

    try:
        with yt_dlp.YoutubeDL(ydl_opts_extract) as ydl:
            info = ydl.extract_info(ytUrl,download=False)
            print("got it done")
            # with open("ytdlp_info.json", "w") as f:
            #     json.dump(info, f)
            # Check if we got separate streams or a single combined stream
            if 'requested_formats' in info:
                # Separate streams found (High Quality)
                for f in info['requested_formats']:
                    if f['vcodec'] != 'none':
                        video_url = f['url']
                    elif f['acodec'] != 'none':
                        audio_url = f['url']
            else:
                # Fallback to single stream if separate ones aren't available
                video_url = info['url']
                audio_url = None
        print(video_url)
        print('\n')
        print(audio_url)
        print(">>> Step 2: Streaming & Cutting with FFmpeg...")
        
        # Build FFmpeg command
        command = ['ffmpeg', '-y']

        # --- INPUT 1: VIDEO ---
        command.extend(['-http_proxy', PROXIES[0]]) # Use Proxy
        command.extend(['-ss', str(startSec)])     # Seek to start
        command.extend(['-t', str(endSec-startSec)])       # End at time
        command.extend(['-i', video_url])          # Video URL

        # --- INPUT 2: AUDIO (If it exists) ---
        if audio_url:
            command.extend(['-http_proxy', PROXIES[0]]) # Use Proxy for audio too
            command.extend(['-ss', str(startSec)])
            command.extend(['-t', str(endSec-startSec)])
            command.extend(['-i', audio_url])
            # Map them together
            command.extend(['-map', '0:v', '-map', '1:a'])
        
        # --- ENCODING SETTINGS ---
        # We use libx264 (Re-encode) to fix the Lag/Black Frame issue.
        # 'ultrafast' makes it very quick on CPU.
        command.extend(['-c:v', 'libx264', '-preset', 'superfast', '-crf', '23'])
        command.extend(['-c:a', 'aac']) # Encode audio to AAC
        
        command.append(outPath)

        # Run FFmpeg
        t1 = time.time()
        subprocess.run(command, check=True)
        print(f"Done! Time taken: {time.time() - t1:.2f}s")

        # SLEEP TO PREVENT BAN
        # time.sleep(random.randint(5, 15))

    except Exception as e:
        print(f"Error: {e}")


def download_and_create_clips(youtube_url: str, startSec: int, endSec: int) -> bool:

    from utility.variables import cookiesFile
    import yt_dlp
    proxy = ""
    ydlOpts = {
        'quiet': True,
        'force_ipv6': True,
        'format': 'best[height<=720][protocol^=http]',
        'cookiefile': cookiesFile, # important
        'js_runtimes': { 'node': {}}, # important  
        'no_warnings': True,
        'extract_flat': False, 
    }
    if proxy:
        ydlOpts['proxy'] = proxy

    videoUrl = None
    audioUrl = None
    
    with yt_dlp.YoutubeDL(ydlOpts) as ydl:
        info = ydl.extract_info(youtube_url, download=False) # download = false is important
        
        # Check if we got separate streams or a single combined stream
        if 'requested_formats' in info:
            # Separate streams found (High Quality)
            for f in info['requested_formats']:
                if f['vcodec'] != 'none':
                    videoUrl = f['url']
                elif f['acodec'] != 'none':
                    audioUrl = f['url']
        else:
            # Fallback to single stream if separate ones aren't available
            # pass that same URL to both video_url and audio_url arguments in ffmpeg command.
            videoUrl = info['url']
            audioUrl = info['url']
  
    process_dual_input_clip(videoUrl, audioUrl, startSec, endSec-startSec)



from home.services import ClipProcessingService
from home.models import ClipRequest
from utility.functions import time_to_seconds
clipProcessingService = ClipProcessingService()
clipRequest = ClipRequest.objects.get(id="02793d9a-27a2-4848-820c-4fc0aafc1915")
youtube_url = "https://www.youtube.com/watch?v=l30e6vUocyc"
            # Prepare output filenames
# out720pPathAbsolute = "720p.mp4"
# out480pPathAbsolute = "480p.mp4"

# startSec = time_to_seconds(str("00:01:00"))
# endSec = time_to_seconds(str("00:01:30"))

# clipDurationSeconds = endSec - startSec
# download_and_create_clips(youtube_url,startSec, endSec)

# proxy = clipProcessingService.download_and_create_clips(clipRequest, startSec, endSec, clipDurationSeconds, out720pPathAbsolute, out480pPathAbsolute)
# print(proxy)

working()