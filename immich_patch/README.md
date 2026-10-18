# Overview
This project is about managing home video/image media.

# Current Apps and Structure
- mobile phone with immich
- nas server with self organized library
- nas server also has immich server

# User stories
- User wants to keep phone synced with immich server
- User wants to view already existing library in immich
- User wants to edit existing library with external tools
    - Change directory structure and move files
    - Rename files
    - Edit files exif including datetime/timzone and gps
- User wants to deploy immich from existing docker image and keep updates automatic
- We can touch the immich server code with hooks or additional containers, but cannot change the app source

# Current problems
- editing a file, changes its hash
- moving a file makes immich lose track
- immich upload and external libraries behave differently. one cannot move media from upload to external

# Possible solutions


## External service vs. hooking into Immich

### A. External service (separate container, talks to Immich via API + DB)
Pros:
- Official Immich image stays untouched
- Updates only need our test suite to pass, no rebasing
- Our code is isolated, simpler to reason about and replace

Cons:
- Writes to Immich's internal DB, which is not a public API and can change
- Must coordinate with Immich jobs from outside (pause queues, disable scans) - race-prone
- Duplicates some logic Immich already has (scanning, path handling)

### B. Hook into Immich (fork / patch the server)
Pros:
- Direct access to Immich's services, jobs and transactions - no races
- Can change core behaviour (move detection, keeping checksum on edit, upload -> external)
- Could be upstreamed, eventually removing the need for custom code

Cons:
- Must build and maintain our own image, no official image
- Every Immich update means rebasing patches and resolving conflicts
- Immich changes fast, internal code is less stable than the DB schema

### C. Monkey patching (official image + runtime patches)
Official image is used as-is; a patch file is mounted into the container and loaded
at startup (e.g. `NODE_OPTIONS=--require /patches/index.js`), wrapping Immich's
service methods (library scan, metadata extraction, upload) before NestJS starts.

Pros:
- Official image, no build, no fork
- Runs in-process: uses Immich's own repositories, jobs and transactions - no races
- Can change core behaviour, same as B

Cons:
- Depends on internal class/method names of compiled code - least stable contract
- Breakage can be silent (patch no longer applies) - needs startup self-check + tests
- Must verify the server's module format allows `--require` hooking (CJS vs ESM)
