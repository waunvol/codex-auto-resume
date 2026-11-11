"""运行相同的 unittest 套件，并把失败堆栈发布为 Actions 注释供排查。"""
from pathlib import Path
import sys
import unittest


def escape(value):
    return str(value).replace('%','%25').replace('\r','%0D').replace('\n','%0A')


if __name__=='__main__':
    suite=unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parent))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    for case,trace in result.failures+result.errors:
        print('::error title=Test failure::'+escape(str(case)+'\n'+trace),flush=True)
    sys.exit(0 if result.wasSuccessful() else 1)
