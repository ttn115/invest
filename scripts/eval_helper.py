import sys, os
sys.path.insert(0, os.path.abspath('.'))
from loguru import logger
logger.remove()
logger.add('logs/eval_6214.log', encoding='utf8')

import io
old_stdout = sys.stdout
sys.stdout = io.StringIO()

from scripts.evaluate_6214 import evaluate_stock
evaluate_stock()

output = sys.stdout.getvalue()
sys.stdout = old_stdout

with open('eval_output.txt', 'w', encoding='utf-8') as f:
    f.write(output)
