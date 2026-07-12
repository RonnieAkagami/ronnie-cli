import sys

try:
    import ollama
    import rich
except ImportError as e:
    print(f"Missing required dependency: {e}")
    print("Please run: pip install rich ollama")
    sys.exit(1)
