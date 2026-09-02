## ADDED Requirements

### Requirement: Runtime maintains its status bar integration

A registered Job Runtime SHALL keep exactly one of its status bar draw callbacks attached to Blender's current status bar Header for the duration of the Runtime registration.

#### Scenario: Initial runtime registration

- **WHEN** a Job Runtime is registered
- **THEN** its status bar draw callback is attached to the current status bar Header

#### Scenario: Ordinary runtime polling

- **WHEN** the registered Runtime polls while its status bar draw callback is already attached
- **THEN** the Runtime does not attach a duplicate callback

### Requirement: Runtime recovers after Blender rebuilds status bar UI

A registered Job Runtime SHALL restore its status bar draw callback when Blender replaces the status bar Header draw state after Runtime registration.

#### Scenario: Dynamic draw state is removed

- **WHEN** Blender rebuilds the status bar Header and removes its dynamic draw callback collection
- **AND** the registered Runtime performs its next main-thread poll
- **THEN** the Runtime attaches its existing status bar draw callback to the current Header

#### Scenario: Callback alone is removed

- **WHEN** the current status bar Header retains dynamic callbacks but no longer contains the registered Runtime's callback
- **AND** the registered Runtime performs its next main-thread poll
- **THEN** the Runtime restores its callback without duplicating other callbacks

### Requirement: Unregistered runtime does not restore status bar UI

An unregistered Job Runtime MUST leave no active status bar draw callback and MUST NOT restore that callback through background polling.

#### Scenario: Runtime unregisters after UI reload

- **WHEN** a registered Runtime is unregistered after Blender has rebuilt the status bar Header
- **THEN** its polling lifecycle stops
- **AND** its status bar callback is absent from the current Header
