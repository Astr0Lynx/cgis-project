# Test runner for ogc-services (unit + external-HTTP integration tests).
FROM python@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
WORKDIR /work
COPY tests/requirements-test.txt /tmp/requirements-test.txt
RUN pip install --no-cache-dir -r /tmp/requirements-test.txt
# Source is bind-mounted at /work by compose; PYTHONPATH covers both packages.
ENV PYTHONPATH=/work/tiler:/work/pygeoapi PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "pytest", "-q", "tests/unit", "tests/integration"]
