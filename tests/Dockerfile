FROM python:3.11-slim
RUN pip install --no-cache-dir fastapi==0.115.0 httpx==0.27.2 boto3==1.35.24 pymysql==1.1.1 cryptography==43.0.1 psycopg2-binary==2.9.9 pymongo==4.10.1
WORKDIR /workspace
CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
