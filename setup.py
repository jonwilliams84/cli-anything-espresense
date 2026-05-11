from setuptools import setup, find_namespace_packages

with open("cli_anything/espresense/README.md") as f:
    long_description = f.read()

setup(
    name="cli-anything-espresense",
    version="0.1.0",
    description="CLI harness for ESPresense — companion + per-node firmware control from the command line",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    install_requires=[
        "click>=8.0.0",
        "prompt-toolkit>=3.0.0",
        "requests>=2.28.0",
        "websocket-client>=1.5.0",
        "ruamel.yaml>=0.18.0",
        "paho-mqtt>=1.6.0",
    ],
    entry_points={
        "console_scripts": [
            "cli-anything-espresense=cli_anything.espresense.espresense_cli:main",
        ],
    },
    package_data={
        "cli_anything.espresense": ["skills/*.md", "README.md"],
    },
    include_package_data=True,
    python_requires=">=3.10",
)
