from setuptools import setup, find_packages

setup(
    name="whatsapp-cli",
    version="1.0.0",
    description="CLI for WhatsApp on macOS — read, send, monitor, auto-reply, export",
    author="Marcel R. G. Berger",
    author_email="hello@marcelrgberger.com",
    packages=find_packages(),
    install_requires=["click>=8.0.0", "prompt-toolkit>=3.0.0"],
    entry_points={"console_scripts": ["whatsapp-cli=whatsapp_cli.whatsapp_cli:main"]},
    python_requires=">=3.10",
)
