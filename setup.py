from setuptools import setup, find_packages

setup(
    name="pixelurgy-vault",
    version="0.1.0",
    description="Pixelurgy Vault REST API server",
    author="Pixelurgy",
    packages=find_packages(),
    install_requires=["fastapi", "uvicorn"],
    python_requires=">=3.7",
)
