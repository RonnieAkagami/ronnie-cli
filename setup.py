from setuptools import setup, find_packages

setup(
    name="ronnie",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "rich>=10.0.0",
        "ollama>=0.2.0",
    ],
    entry_points={
        "console_scripts": [
            "ronnie=ronnie.cli:main",
        ],
    },
)
