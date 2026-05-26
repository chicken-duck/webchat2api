#!/usr/bin/env python
import sys
import os
sys.path.insert(0, '/app/webchat2api')
os.chdir('/app/webchat2api')
import unittest
from test.test_image_task_service import ImageTaskServiceTests

output_file = '/app/webchat2api/test_results.txt'

with open(output_file, 'w') as f:
    class StreamToFile:
        def __init__(self, file):
            self.file = file
        def write(self, text):
            self.file.write(text)
            self.file.flush()
        def flush(self):
            self.file.flush()
    
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(ImageTaskServiceTests)
    runner = unittest.TextTestRunner(verbosity=2, stream=StreamToFile(f))
    result = runner.run(suite)
    
    f.write(f'\n\nTests run: {result.testsRun}\n')
    f.write(f'Failures: {len(result.failures)}\n')
    f.write(f'Errors: {len(result.errors)}\n')
    f.write(f'Success: {result.wasSuccessful()}\n')
    
    if result.failures:
        f.write('\nFAILURES:\n')
        for test, traceback in result.failures:
            f.write(f'{test}:\n{traceback}\n')
    
    if result.errors:
        f.write('\nERRORS:\n')
        for test, traceback in result.errors:
            f.write(f'{test}:\n{traceback}\n')

print(f"Test results written to {output_file}")
sys.exit(0 if result.wasSuccessful() else 1)
