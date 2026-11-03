# Frozen buggy shop fixture

This is the event-built shop snapshot from commit 24f72ad, before the first approved agent patch. It is a test asset for reproduction, patch and apply integration checks. Keeping it separate lets examples/shop evolve through approved fixes without invalidating engine tests that require a known bug.

The nested baseline tests run only when copied into a temporary project by engine tests. Root pytest discovery excludes fixtures directories. This asset contains synthetic data and no third-party project code.
