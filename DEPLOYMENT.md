# Meow Now - Production Deployment Guide

This guide walks you through deploying a complete, production-ready Meow Now phone tree system with Asterisk.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start](#quick-start)
3. [Step-by-Step Setup](#step-by-step-setup)
4. [SIP Provider Configuration](#sip-provider-configuration)
5. [Testing](#testing)
6. [Troubleshooting](#troubleshooting)
7. [Production Checklist](#production-checklist)

---

## Prerequisites

### Hardware Requirements
- Linux server (Unraid, Ubuntu, Debian, etc.)
- 2+ CPU cores
- 4GB+ RAM
- 10GB+ disk space
- Stable internet connection

### Software Requirements
- Docker and Docker Compose installed
- Port forwarding access on your router
- Static IP or DDNS hostname

### Account Requirements
- **Phone Number** from a SIP provider:
  - [NumberBarn](https://www.numberbarn.com/) ($2-5/month) - Recommended
  - [VoIP.ms](https://voip.ms)
  - [Flowroute](https://www.flowroute.com/)
  - Twilio (SIP trunking)

---

## Quick Start

For experienced users, here's the fast path:

```bash
# 1. Clone and setup
git clone <repo-url>
cd meow-now
cp .env.example .env

# 2. Generate audio files
python3 scripts/setup_audio.py

# 3. Configure SIP credentials
nano config/asterisk/sip.conf
# Add your NumberBarn or SIP provider details

# 4. Update external IP
nano config/asterisk/sip.conf
# Set externip=YOUR_PUBLIC_IP

# 5. Deploy
docker-compose up -d

# 6. Verify
docker-compose logs -f
# Check that both containers started successfully

# 7. Configure port forwarding
# Forward ports 5060 UDP and 10000-10099 UDP to your server

# 8. Test
# Call your phone number!
```

---

## Step-by-Step Setup

### Step 1: Clone Repository

```bash
# SSH into your server
ssh user@your-server-ip

# Clone repository
cd /opt  # or your preferred location
git clone <repo-url> meow-now
cd meow-now

# Verify files
ls -la
```

### Step 2: Install Dependencies (If Testing Locally)

```bash
# Install Python dependencies
pip3 install -r requirements.txt

# Install system dependencies
sudo apt-get update
sudo apt-get install -y espeak-ng sox libsndfile1
```

### Step 3: Generate Audio Files

```bash
# Run the audio setup script
python3 scripts/setup_audio.py

# This generates:
# - 5 realistic cat meow samples (short, long, trill, chirp, yowl)
# - 7 IVR audio prompts (welcome, menu, instructions, etc.)

# Verify audio files
ls -lh audio/meow_samples/
ls -lh audio/prompts/
```

**Optional:** Replace generated prompts with professional recordings:
```bash
# Record your own prompts with professional voice
# Save them as 8kHz mono WAV files
# Replace files in audio/prompts/
```

### Step 4: Configure Environment

```bash
# Copy environment template
cp .env.example .env

# Edit configuration
nano .env
```

Key settings to configure:
```bash
# Change in production!
SECRET_KEY=your-random-secret-key-change-me

# Ollama (optional - for dynamic cat responses)
OLLAMA_URL=http://your-ollama-host:11434

# TTS Engine
TTS_ENGINE=piper  # or espeak for simpler voices

# Cat Personalities (enable/disable)
ENABLE_GRUMPY_CAT=True
ENABLE_WISE_CAT=True
ENABLE_ANXIOUS_CAT=True
ENABLE_DIVA_CAT=True

# Logging
LOG_LEVEL=INFO
```

### Step 5: Configure Asterisk SIP

This is the most critical step for connecting to your phone number.

#### 5A. Get Your Public IP

```bash
curl ifconfig.me
# Note this IP address
```

#### 5B. Update sip.conf

```bash
nano config/asterisk/sip.conf
```

Update the following sections:

```ini
[general]
; Your public IP or DDNS hostname
externip=YOUR.PUBLIC.IP.ADDRESS
; Or use:
; externhost=your-hostname.ddns.net
; externrefresh=60

; Local network (adjust to your LAN)
localnet=192.168.1.0/255.255.255.0  ; leak-guard-allow: example RFC 1918 range, not a host

; SIP and RTP ports
bindport=5060
rtpstart=10000
rtpend=10099

; Disable SIP ALG workarounds if you have control
nat=force_rport,comedia

[numberbarn]
; NumberBarn SIP trunk configuration
type=peer
host=sip.numberbarn.com  ; Your provider's SIP server
username=YOUR_NUMBERBARN_USERNAME
secret=YOUR_NUMBERBARN_PASSWORD
fromdomain=sip.numberbarn.com
fromuser=YOUR_NUMBERBARN_USERNAME
context=from-trunk
insecure=port,invite
qualify=yes
nat=force_rport,comedia
canreinvite=no
dtmfmode=rfc2833
```

**Important:** Replace `YOUR_NUMBERBARN_USERNAME` and `YOUR_NUMBERBARN_PASSWORD` with your actual credentials from NumberBarn.

#### 5C. Update extensions.conf

```bash
nano config/asterisk/extensions.conf
```

Update your DID number:
```ini
[from-numberbarn]
; Replace 15551234567 with your actual phone number
exten => 15551234567,1,NoOp(Meow-Now call from ${CALLERID(num)})
 same => n,Goto(from-trunk,${EXTEN},1)
```

### Step 6: Configure Router Port Forwarding

You must forward these ports from your router to your server:

| Port Range | Protocol | Service |
|------------|----------|---------|
| 5060 | UDP | SIP signaling |
| 10000-10099 | UDP | RTP audio |

**Router Configuration Steps:**

1. Log into your router (usually http://192.168.1.1) <!-- leak-guard-allow: common router default, not a real host -->
2. Find "Port Forwarding" or "Virtual Server" settings
3. Add two rules:
   - **SIP**: External 5060 UDP → Internal SERVER_IP:5060 UDP
   - **RTP**: External 10000-10099 UDP → Internal SERVER_IP:10000-10099 UDP
4. Save and reboot router if needed

**Verify ports are open:**
```bash
# From external network (use your phone's cellular data)
nmap -sU -p 5060 YOUR_PUBLIC_IP
nmap -sU -p 10000-10010 YOUR_PUBLIC_IP
```

### Step 7: Configure SIP Provider

#### For NumberBarn:

1. Log into [NumberBarn](https://www.numberbarn.com/)
2. Go to your phone number settings
3. Select "Call Forwarding"
4. Choose forwarding type: **SIP URI**
5. Enter: `sip:YOUR_PUBLIC_IP:5060`
   - Or: `sip:your-hostname.ddns.net:5060`
6. Enable forwarding
7. Save settings

#### For VoIP.ms or other providers:

See `docs/NUMBERBARN.md` for provider-specific instructions.

### Step 8: Deploy with Docker

```bash
# Build and start containers
docker-compose up -d

# View logs
docker-compose logs -f

# Check container status
docker ps

# You should see:
# - meow-now (Python application)
# - meow-asterisk (Asterisk PBX)
```

**Verify services are running:**
```bash
# Check Flask web interface
curl http://localhost:5000/health

# Check AGI server
docker logs meow-now | grep "AGI Server listening"

# Check Asterisk
docker exec -it meow-asterisk asterisk -rx "core show version"
```

### Step 9: Verify SIP Registration

```bash
# Check if Asterisk registered with your SIP provider
docker exec -it meow-asterisk asterisk -rx "sip show registry"

# Expected output:
# Host                 Username         Refresh  State
# sip.numberbarn.com   your-username    105      Registered
```

**If registration fails:**
- Double-check username/password in `config/asterisk/sip.conf`
- Verify port 5060 is forwarded correctly
- Check firewall rules
- See troubleshooting section below

### Step 10: Test the System

#### Test 1: Web Interface
```bash
# From your browser
http://YOUR_SERVER_IP:5000

# Should show Meow Now status page
```

#### Test 2: Test Call
1. **Call your phone number** from your mobile phone (use cellular, not WiFi)
2. You should hear the welcome prompt
3. Press **1** for Meow Mockery:
   - Speak for a few seconds
   - Press **#** or wait
   - Hear cat meows mocking your voice!
4. Or press **2** for Talkative Cats:
   - Hear a random cat personality monologue

#### Test 3: Check Logs
```bash
# Watch logs in real-time
docker-compose logs -f

# Look for:
# - "Incoming call from..." (Asterisk detected call)
# - "AGI Server listening" (AGI server running)
# - "Playing main menu" (IVR started)
# - "Caller selected: Meow Mockery" (Option 1 chosen)
# - "Recording caller speech" (Voice recording)
# - "Generated meow mockery" (Meow generation succeeded)
```

---

## SIP Provider Configuration

### NumberBarn (Recommended)

**Advantages:**
- Simple setup
- Low cost ($2-5/month)
- SIP forwarding included
- No per-minute charges when self-hosting

**Configuration:**
1. Purchase number at numberbarn.com
2. In number settings: Call Forwarding → SIP URI
3. Enter: `sip:YOUR_IP:5060`
4. Update `config/asterisk/sip.conf` with credentials

### VoIP.ms

**Advantages:**
- Very flexible
- Many features
- Good for developers

**Configuration:**
1. Get account at voip.ms
2. Create sub-account for SIP registration
3. Configure DID routing to sub-account
4. Update `config/asterisk/sip.conf`:
```ini
[voipms]
type=peer
host=YOUR_POP.voip.ms
username=YOUR_SUBACCOUNT
secret=YOUR_PASSWORD
context=from-trunk
; ... rest of config
```

### Twilio SIP Trunking

**Advantages:**
- Reliable
- Good documentation
- Scales well

**Configuration:**
1. Set up SIP trunk in Twilio console
2. Add your public IP to allowed list
3. Configure trunk credentials
4. Route phone number to trunk
5. Update Asterisk config with trunk details

---

## Testing

### Local Testing (Without Phone Number)

You can test the IVR system locally using the test interface:

```bash
# Install additional dependencies
pip3 install pyaudio

# Run test interface
python3 test_interface.py

# Open browser to:
http://localhost:5001

# Use your microphone to test the system!
```

### Testing Call Quality

1. **Audio clarity**: Should be clear, not choppy
2. **DTMF detection**: Key presses should be recognized
3. **Meow quality**: Should sound cat-like, not robotic
4. **No echo**: Audio should be clean

### Testing Performance

```bash
# Monitor CPU usage during call
htop

# Monitor memory
free -h

# Check call handling capacity
# Make multiple concurrent test calls
```

---

## Troubleshooting

### No Incoming Calls

**Symptom:** Calls don't reach your system

**Fixes:**
1. Verify port forwarding:
   ```bash
   nmap -sU -p 5060 YOUR_PUBLIC_IP
   ```
2. Check SIP registration:
   ```bash
   docker exec -it meow-asterisk asterisk -rx "sip show registry"
   ```
3. Verify SIP provider settings
4. Check firewall rules:
   ```bash
   sudo ufw status
   # Allow ports if needed:
   sudo ufw allow 5060/udp
   sudo ufw allow 10000:10099/udp
   ```

### One-Way Audio

**Symptom:** You can't hear prompts or caller can't hear you

**Fixes:**
1. Verify RTP ports forwarded (10000-10099)
2. Check `externip` in `sip.conf`
3. Disable SIP ALG on router
4. Check NAT settings in `sip.conf`:
   ```ini
   nat=force_rport,comedia
   ```

### Meows Sound Robotic

**Fixes:**
1. Regenerate meow samples with more variety
2. Adjust pitch variance in `.env`:
   ```bash
   MEOW_PITCH_VARIANCE=0.4
   ```
3. Check voice analysis logs for poor pitch detection

### Cats Not Talking

**Symptom:** Option 2 doesn't work

**Fixes:**
1. Check TTS engine is installed:
   ```bash
   docker exec -it meow-now which espeak-ng
   ```
2. Verify audio files exist:
   ```bash
   ls -lh audio/cats/
   ls -lh audio/generated/
   ```
3. Check logs for TTS errors:
   ```bash
   docker logs meow-now | grep TTS
   ```

### Asterisk Won't Start

**Fixes:**
1. Check configuration syntax:
   ```bash
   docker exec -it meow-asterisk asterisk -rx "core show config"
   ```
2. Review Asterisk logs:
   ```bash
   docker exec -it meow-asterisk tail -f /var/log/asterisk/full
   ```
3. Verify ports not in use:
   ```bash
   netstat -tulpn | grep 5060
   ```

### High CPU Usage

**Symptom:** System slows down during calls

**Fixes:**
1. Reduce audio processing:
   ```bash
   # In .env
   PITCH_DETECTION_METHOD=aubio  # Lighter than praat
   ```
2. Disable Ollama for static responses
3. Use Piper instead of Coqui for TTS
4. Limit concurrent calls in Asterisk

---

## Production Checklist

### Security
- [ ] Changed `SECRET_KEY` in `.env`
- [ ] Changed Asterisk AMI password in `config/asterisk/manager.conf`
- [ ] Port 5000 NOT exposed to internet (only 5060 and RTP ports)
- [ ] Strong SIP passwords
- [ ] Firewall configured (allow only necessary ports)
- [ ] Consider fail2ban for SIP brute force protection

### Configuration
- [ ] `.env` file configured with production values
- [ ] `externip` set correctly in `sip.conf`
- [ ] SIP credentials configured
- [ ] DID number updated in `extensions.conf`
- [ ] Router port forwarding configured
- [ ] DDNS configured (if using dynamic IP)

### Audio
- [ ] All IVR prompts generated
- [ ] Cat meow samples sound realistic
- [ ] Audio format correct (8kHz WAV)
- [ ] Prompts tested for clarity

### Testing
- [ ] Test call from external phone works
- [ ] Option 1 (Meow Mockery) works
- [ ] Option 2 (Talkative Cats) works
- [ ] DTMF tones detected correctly
- [ ] Audio quality is good
- [ ] No echo or feedback
- [ ] Calls hang up properly

### Monitoring
- [ ] Web interface accessible internally
- [ ] Health check endpoint responding
- [ ] Logs being written correctly
- [ ] Log rotation configured
- [ ] Disk space monitoring set up
- [ ] Optional: Uptime monitoring configured

### Documentation
- [ ] Configuration backed up
- [ ] Custom changes documented
- [ ] Team trained on system (if applicable)

---

## Post-Deployment

### Maintenance Tasks

**Daily:**
- Check for unusual call patterns
- Verify system is responding

**Weekly:**
- Review logs for errors
- Test a call to ensure functionality
- Check disk space

**Monthly:**
- Clean up old recordings: `rm audio/recordings/*`
- Clean up generated files: `rm audio/generated/*`
- Review and update cat personalities
- Check for software updates

### Updating the System

```bash
# Pull latest changes
cd /opt/meow-now
git pull

# Rebuild containers
docker-compose down
docker-compose build
docker-compose up -d

# Verify everything works
docker-compose logs -f
```

### Backup

```bash
# Backup configuration
tar czf meow-now-backup-$(date +%Y%m%d).tar.gz \
    .env \
    config/ \
    audio/ \
    logs/

# Store backup securely
mv meow-now-backup-*.tar.gz /backup/location/
```

---

## Getting Help

If you encounter issues:

1. **Check logs:**
   ```bash
   docker-compose logs -f
   docker logs meow-asterisk
   ```

2. **Enable debug mode:**
   ```bash
   # In .env
   LOG_LEVEL=DEBUG
   docker-compose restart
   ```

3. **Review documentation:**
   - QUICKSTART.md
   - TESTING.md
   - docs/SETUP.md
   - docs/NUMBERBARN.md

4. **Search existing issues:**
   - GitHub Issues

5. **Create new issue:**
   - Include logs (last 50 lines)
   - Describe your setup
   - Steps to reproduce
   - Expected vs actual behavior

---

## Success!

If you've made it this far and everything works:

🎉 **Congratulations!** Your Meow Now phone tree is live!

Now you can:
- Give friends your number and surprise them with cat meows
- Customize cat personalities
- Add new menu options
- Build other voice applications on this platform

**Made with ❤️ and 😺 by cat enthusiasts!**
