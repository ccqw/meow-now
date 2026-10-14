# Meow Now - Project Configuration

> Self-Hosted Cat Voicemail System - Claude AI Assistant Configuration

## Project Overview

**Name**: Meow Now
**Type**: Telephony Application / Voice IVR System
**Tech Stack**: Python, Asterisk, Docker, Flask
**Deployment**: Self-hosted (Unraid, VPS, home server)
**Status**: ✅ Production Ready

### What This Does

Meow Now turns a real phone number into an interactive cat-themed voice experience. Callers can:
1. **Meow Mockery** - Voice-to-meow converter that matches caller's pitch/rhythm
2. **Talkative Cats** - AI-powered cat personalities deliver 15-second monologues

### Key Technologies

- **Asterisk**: Open-source PBX for phone calls (SIP/RTP)
- **AGI**: Asterisk Gateway Interface (Python ↔ Asterisk)
- **Flask**: Web server for status and health checks
- **Docker**: Containerized deployment
- **GitHub Actions**: CI/CD for multi-arch builds
- **Ollama** (optional): Local LLM for dynamic cat responses

---

## Architecture Quick Reference

```
Phone Call (SIP) → Asterisk Container → AGI Server (Python)
                                              ↓
                                     IVR Handler (menu)
                                              ↓
                          ┌───────────────────┴────────────────────┐
                          │                                        │
                    Meow Mockery                          Talkative Cats
                          │                                        │
                    Voice Analyzer                          Cat Personalities
                          │                                        │
                    Meow Generator                          TTS + Ollama
                          │                                        │
                    Play Audio ← ────────────────────────────── Play Audio
```

### Key File Locations

- **Core App**: `app.py` (Flask), `agi_server.py` (AGI)
- **Services**: `services/ivr.py`, `services/meow_generator.py`, `services/cat_personalities.py`
- **Config**: `config/asterisk/` (dialplan, SIP), `.env` (app settings)
- **Audio**: `audio/prompts/` (IVR), `audio/meow_samples/` (cat sounds)
- **Scripts**: `scripts/setup_audio.py`, `scripts/deploy.sh`
- **Docs**: `DEPLOYMENT.md`, `docs/UNRAID.md`, `ARCHITECTURE.md`

---

## Development Guidelines

### When Working on This Project

1. **Read Documentation First**
   - `ARCHITECTURE.md` - System design
   - `DEPLOYMENT.md` - Production setup
   - `GITHUB_ISSUES.md` - Roadmap and tasks
   - `docs/UNRAID.md` - Unraid deployment

2. **Use TodoWrite for Multi-Step Tasks**
   - Any task with 3+ steps
   - Track progress in real-time
   - Update status immediately

3. **Test Changes**
   - Audio: `python scripts/setup_audio.py`
   - Docker: `docker build .`
   - Deployment: `./scripts/deploy.sh`
   - Integration: Make a test call

4. **Commit Often**
   - Descriptive commit messages
   - Reference GitHub issues
   - Don't commit secrets (.env, SIP credentials)

### Common Tasks

#### Add New Cat Personality

1. Edit `services/cat_personalities.py`
2. Create new class inheriting from `CatPersonality`
3. Define personality traits and fallback monologues
4. Add to `CAT_REGISTRY`
5. Enable in `.env`: `ENABLE_YOUR_CAT=True`
6. Test with local call or test_interface.py

#### Modify IVR Flow

1. Edit `services/ivr.py`
2. Update dialplan if needed: `config/asterisk/extensions.conf`
3. Regenerate audio prompts if text changed
4. Test call flow end-to-end

#### Update Audio Prompts

1. Edit text in `scripts/setup_audio.py`
2. Run: `python scripts/setup_audio.py`
3. Or record professional audio (8kHz WAV mono)
4. Place in `audio/prompts/`

#### Configure SIP Provider

1. Edit `config/asterisk/sip.conf`
2. Set `externip` to your public IP
3. Add provider credentials in `[provider]` section
4. Update `extensions.conf` with DID number
5. Restart Asterisk: `docker restart meow-asterisk`

---

## GitHub Issues - Context Offloading

All project planning, features, and tasks are tracked in GitHub Issues. See `GITHUB_ISSUES.md` for the complete list.

### Issue Categories

**High Priority (Must Complete):**
- #1: Realistic Cat Meow Generation ✅ DONE
- #2: IVR Audio Prompts ✅ DONE
- #3: Docker Containerization ✅ DONE
- #4: End-to-End Testing

**Medium Priority (Production Ready):**
- #5: SIP Trunk Configuration
- #6: TTS Implementation
- #7: Configuration Management
- #8: Deployment Documentation ✅ DONE

**Enhancement (Post-Launch):**
- #9: Web Dashboard
- #10: Call Recording Storage
- #11: Multi-Language Support
- #12: Multiple SIP Providers
- #13: SMS Notifications
- #14: Voice Cloning
- #15: Analytics

**Quality (Ongoing):**
- #16: Automated Tests
- #17: Performance Testing
- #18: Security Audit
- #19: Video Tutorial
- #20: Code Documentation

### Creating New Issues

When creating GitHub issues:
- Use labels: `enhancement`, `bug`, `documentation`, `security`
- Reference related files and line numbers
- Include acceptance criteria
- Link to relevant documentation

---

## Environment & Configuration

### Environment Variables

Required in `.env`:
```bash
# Flask
SECRET_KEY=your-secret-key-change-me
FLASK_ENV=production

# AGI Server
AGI_HOST=0.0.0.0
AGI_PORT=4573

# Audio
SAMPLE_RATE=8000
MAX_RECORDING_DURATION=60
CAT_MONOLOGUE_DURATION=15

# TTS
TTS_ENGINE=piper  # or espeak-ng

# Ollama (optional)
OLLAMA_URL=http://YOUR_OLLAMA_HOST:11434

# Cat Personalities
ENABLE_GRUMPY_CAT=True
ENABLE_WISE_CAT=True
ENABLE_ANXIOUS_CAT=True
ENABLE_DIVA_CAT=True
```

### Asterisk Configuration

**`config/asterisk/sip.conf`:**
- Set `externip` to your public IP
- Configure SIP provider credentials
- Set NAT settings: `nat=force_rport,comedia`

**`config/asterisk/extensions.conf`:**
- Update DID number to your phone number
- Configure call routing to AGI server

---

## Testing

### Local Testing (No Phone Required)

```bash
# Test audio generation
python scripts/setup_audio.py

# Test web interface
python app.py
# Visit: http://localhost:5000

# Test with microphone (requires pyaudio)
python test_interface.py
# Visit: http://localhost:5001
```

### Integration Testing

```bash
# Deploy locally
docker-compose up -d

# Check status
docker ps
docker logs meow-now
docker logs meow-asterisk

# Verify SIP registration
docker exec -it meow-asterisk asterisk -rx "sip show registry"

# Make test call
# Call your phone number from mobile (use cellular, not WiFi)
```

### Debugging

```bash
# View real-time logs
docker-compose logs -f

# Enter container
docker exec -it meow-now bash
docker exec -it meow-asterisk bash

# Check Asterisk CLI
docker exec -it meow-asterisk asterisk -rvvv

# Test audio files
ls -lh audio/prompts/
ls -lh audio/meow_samples/
```

---

## Deployment Targets

### Unraid (Primary Target)

- One-click install via Community Applications
- Use `docker-compose.unraid.yml`
- See `docs/UNRAID.md` for complete guide

### VPS / Cloud

- DigitalOcean, Linode, Vultr, Hetzner
- Oracle Cloud Free Tier (recommended)
- Use `docker-compose.yml`
- See `DEPLOYMENT.md` for setup

### Home Server

- Any Linux with Docker
- Requires port forwarding (5060, 10000-10099 UDP)
- DDNS recommended (DuckDNS)
- See `DEPLOYMENT.md`

---

## CI/CD Pipeline

### GitHub Actions Workflow

**Triggers:**
- Push to main/master
- Version tags (v*.*.*)
- Pull requests (build only, no push)

**Outputs:**
- `ghcr.io/quadseven/meow-now:latest`
- `ghcr.io/quadseven/meow-now:v1.0.0`
- `ghcr.io/quadseven/meow-now:main`

**Architectures:**
- linux/amd64 (x86_64)
- linux/arm64 (Raspberry Pi, ARM servers)

**Features:**
- Build caching for faster builds
- Attestation for supply chain security
- Automatic versioning from git tags

---

## Project Conventions

### Naming Conventions

- **Files**: `snake_case.py`
- **Classes**: `PascalCase`
- **Functions**: `snake_case()`
- **Variables**: `snake_case`
- **Constants**: `UPPER_SNAKE_CASE`

### Directory Structure

```
meow-now/
├── .claude/              # AI assistant configuration
├── .github/workflows/    # CI/CD automation
├── audio/               # Audio files (generated)
├── config/              # Configuration files
│   └── asterisk/       # Asterisk configs
├── docs/               # Documentation
├── models/             # TTS models (downloaded)
├── scripts/            # Setup and utility scripts
├── services/           # Core service modules
└── logs/               # Application logs
```

### Code Comments

- Use docstrings for all functions/classes
- Inline comments for complex logic only
- TODO comments reference GitHub issues
- Include examples in docstrings

---

## Security Considerations

### Never Commit

- `.env` files
- SIP credentials
- API keys
- Private keys
- Audio recordings (from real callers)

### Always Check

- Port forwarding is secure
- SIP passwords are strong
- Flask SECRET_KEY is randomized
- Asterisk AMI password changed from default
- Firewall rules are restrictive

### Best Practices

- Run fail2ban for SIP brute force protection
- Don't expose Flask port (5000) to internet
- Use VPN for administrative access
- Regular security updates
- Monitor logs for suspicious activity

---

## Common Issues & Solutions

### SIP Registration Fails

1. Check `externip` in `sip.conf`
2. Verify port 5060 UDP is forwarded
3. Check SIP provider credentials
4. Test: `docker exec meow-asterisk asterisk -rx "sip show registry"`

### No Incoming Calls

1. Verify port forwarding on router
2. Check SIP provider forwarding configuration
3. Ensure public IP is correct
4. Test with nmap: `nmap -sU -p 5060 YOUR_IP`

### One-Way Audio

1. Forward RTP ports: 10000-10099 UDP
2. Set NAT settings: `nat=force_rport,comedia`
3. Verify `externip` is correct
4. Disable SIP ALG on router

### Meows Sound Robotic

1. Regenerate with more variance: `MEOW_PITCH_VARIANCE=0.4`
2. Use RealMeowGenerator instead of MeowSynthesizer
3. Add more meow samples to `audio/meow_samples/`

---

## Resources

### Official Documentation

- [Asterisk Docs](https://docs.asterisk.org/)
- [Flask Docs](https://flask.palletsprojects.com/)
- [Docker Docs](https://docs.docker.com/)
- [GitHub Actions](https://docs.github.com/en/actions)

### This Project

- **Main**: README.md
- **Setup**: DEPLOYMENT.md, docs/UNRAID.md
- **Development**: ARCHITECTURE.md, GITHUB_ISSUES.md
- **Testing**: TESTING.md, CHECKLIST.md

### SIP Providers

- [NumberBarn](https://www.numberbarn.com/) - Recommended
- [VoIP.ms](https://voip.ms)
- [Flowroute](https://www.flowroute.com/)

---

## AI Assistant Notes

### Context for Future Sessions

This project is **production-ready** and **deployed** via CI/CD. Main areas of focus:

1. **GitHub Issues** contain all planning and tasks
2. **Audio files** are generated, not committed
3. **Docker images** are pre-built on ghcr.io
4. **Unraid deployment** is the primary target
5. **Documentation** is comprehensive and up-to-date

### When User Asks About:

- **"How do I deploy?"** → Point to `DEPLOYMENT.md` or `docs/UNRAID.md`
- **"How does it work?"** → Refer to `ARCHITECTURE.md`
- **"What's the roadmap?"** → Check `GITHUB_ISSUES.md`
- **"How do I test?"** → See `TESTING.md`
- **"It's not working"** → Check common issues section above

### Quick Commands

```bash
# Generate audio
python scripts/setup_audio.py

# Deploy
./scripts/deploy.sh

# Test
docker-compose up -d && docker logs -f meow-now

# Debug Asterisk
docker exec -it meow-asterisk asterisk -rvvv
```

---

**Last Updated**: 2025-11-15
**Version**: 1.0.0
**Status**: Production Ready
**CI/CD**: ✅ Active
**Unraid Template**: ✅ Ready
