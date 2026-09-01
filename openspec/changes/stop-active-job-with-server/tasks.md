## 1. Stop Lifecycle

- [x] 1.1 Add Runtime tests for an active submitted Job, an idle Server and a stop failure, verifying cancel → Server stop → single cleanup ordering, cleared `active_job`, UI redraw and preserved manual-start state.
- [x] 1.2 Add the Runtime stop boundary and update the generated Stop Server Operator to use it, preserving the idle stop path.
- [x] 1.3 Add operator lifecycle tests for stopping during an in-flight submission and for the next Modal event returning `CANCELLED` without duplicate cleanup.

## 2. Documentation and Release

- [x] 2.1 Update the Chinese and English Blender integration documents with the active-Job Stop Server lifecycle.
- [x] 2.2 Bump the BlendJob package version and synchronize the lock metadata.

## 3. Verification

- [x] 3.1 Run the complete BlendJob test suite.
- [x] 3.2 Build the wheel and verify its filename, metadata version and Runtime stop implementation.
