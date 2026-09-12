#!/usr/bin/env python3
from setuptools import setup, find_packages

setup(
    name="meeting-documentation-engine",
    version="1.0.0",
    description="Multimodal Meeting Documentation & Knowledge Base Engine",
    author="DeepBlue",
    packages=find_packages(),
    py_modules=["process_meeting"],
    install_requires=[
        "opencv-python-headless>=4.8.0",
        "pillow>=10.0.0",
        "imagehash>=4.3.1",
        "webvtt-py>=0.5.1",
    ],
    entry_points={
        "console_scripts": [
            "process-meeting=process_meeting:main",
        ],
    },
    python_requires=">=3.9",
)
