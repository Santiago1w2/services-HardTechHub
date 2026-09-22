FROM hardtech-review-identity
RUN pip install --no-cache-dir httpx==0.27.2 boto3==1.35.24 pymysql==1.1.1
WORKDIR /workspace
CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
