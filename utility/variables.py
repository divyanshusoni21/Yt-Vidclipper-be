import pytz
from yt_helper.settings import ADMIN_EMAIL,FRONTEND_URL,BREVO_API_KEY,DEFAULT_PASSWORD,PROXIES,COOKIES_FILE


istTimezone = pytz.timezone('Asia/Kolkata')
utcTimezone = pytz.timezone('utc')


projectName = 'YT Vidclipper'
adminEmail = ADMIN_EMAIL
frontendDomain = FRONTEND_URL
brevoApiKey = BREVO_API_KEY
defaultPassword = DEFAULT_PASSWORD
projectLogo ="https://i.ibb.co/W4ph4tnj/Screenshot-2026-01-04-at-3-45-21-PM.png"
proxies = PROXIES
cookiesFile = COOKIES_FILE