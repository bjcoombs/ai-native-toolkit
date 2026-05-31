---
name: java-conventions
description: Java conventions for this repo. TRIGGER when editing any *.java file, JUnit tests, or Maven modules.
---

# Java Conventions

Use Log4j2 for logging. Prefer constructor injection over field injection
because it makes dependencies explicit and testable.

Default to JUnit5 with AssertJ for assertions. Run `mvn verify` before pushing.
