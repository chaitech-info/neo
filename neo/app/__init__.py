from dotenv import load_dotenv

# .env (OPENAI_API_KEY, DATABASE_URL, CRM_*, REDIS_URL) app-in istənilən
# giriş nöqtəsindən (uvicorn, celery, scripts) əvvəl yüklənsin.
load_dotenv()
