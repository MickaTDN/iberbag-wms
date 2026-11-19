"""Trusted protected release root, launched with -I -B by the SCM host."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
if __name__=='__main__':
    from iberbag.windows_service import main
    raise SystemExit(main())
