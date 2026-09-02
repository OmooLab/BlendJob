## 1. Status Bar Recovery Tests

- [x] 1.1 Add Runtime tests that simulate Blender replacing `STATUSBAR_HT_header.draw` after registration and verify the next poll restores the existing callback.
- [x] 1.2 Add coverage that repeated polling does not duplicate the callback and unregistration does not leave or restore it.

## 2. Runtime Recovery

- [x] 2.1 Add focused Runtime helpers for detecting, attaching, and safely removing the status bar draw callback.
- [x] 2.2 Use the helpers during registration, main-thread polling, and unregistration while preserving the existing Operator and timer lifecycle.

## 3. Verification

- [x] 3.1 Run the BlendJob test suite and confirm the status bar recovery scenarios and existing Runtime behavior pass.
