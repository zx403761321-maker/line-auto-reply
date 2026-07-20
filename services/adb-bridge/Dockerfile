FROM python:3.11-slim

# 系统依赖（宿主 ADB 运行时所需）
RUN apt-get update && apt-get install -y \
    libusb-1.0-0 libbrotli1 liblz4-1 libprotobuf32t64 \
    libzstd1 libudev1 libcap2 libxxhash0 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 从宿主机预复制的 ADB 二进制和 android 库
COPY adb-host /usr/local/bin/adb
COPY adb-libs-host/ /usr/local/lib/android/
RUN chmod +x /usr/local/bin/adb && \
    echo "/usr/local/lib/android" > /etc/ld.so.conf.d/android.conf && \
    ldconfig

COPY . .

EXPOSE 8899

CMD ["python", "main.py"]
