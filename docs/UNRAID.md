# Meow Now - Unraid Deployment Guide

Complete guide for deploying Meow Now on Unraid servers with one-click installation.

## Quick Install (Recommended)

### Method 1: Unraid Community Applications (Coming Soon)

1. In Unraid, go to **Apps** tab
2. Search for "Meow Now"
3. Click **Install**
4. Configure settings (see Configuration section below)
5. Click **Apply**

### Method 2: Docker Compose (Available Now)

```bash
# 1. Create appdata directory
mkdir -p /mnt/user/appdata/meow-now/{audio,logs,models,asterisk}

# 2. Download configuration
cd /mnt/user/appdata/meow-now
wget https://raw.githubusercontent.com/quadseven/meow-now/main/docker-compose.unraid.yml -O docker-compose.yml
wget https://raw.githubusercontent.com/quadseven/meow-now/main/.env.example -O .env

# 3. Copy Asterisk config
mkdir -p asterisk/config
cd /tmp
git clone https://github.com/quadseven/meow-now.git
cp -r meow-now/config/asterisk/* /mnt/user/appdata/meow-now/asterisk/config/

# 4. Edit configuration
nano /mnt/user/appdata/meow-now/.env
nano /mnt/user/appdata/meow-now/asterisk/config/sip.conf

# 5. Deploy with docker-compose
cd /mnt/user/appdata/meow-now
docker-compose up -d

# 6. Generate audio files
docker exec -it meow-now python scripts/setup_audio.py
```

### Method 3: Manual Docker Template

1. In Unraid, go to **Docker** tab
2. Click **Add Container**
3. Set **Template Type** to "Advanced View"
4. Fill in the following:

```
Name: MeowNow
Repository: ghcr.io/quadseven/meow-now:latest
Network Type: bridge

Port Mappings:
  - Container Port: 5000, Host Port: 5000, Protocol: TCP (Web UI)
  - Container Port: 4573, Host Port: 4573, Protocol: TCP (AGI Server)

Path Mappings:
  - Container Path: /app/audio, Host Path: /mnt/user/appdata/meow-now/audio
  - Container Path: /app/logs, Host Path: /mnt/user/appdata/meow-now/logs
  - Container Path: /app/models, Host Path: /mnt/user/appdata/meow-now/models

Environment Variables:
  - LOG_LEVEL = INFO
  - TTS_ENGINE = piper
  - ENABLE_GRUMPY_CAT = True
  - ENABLE_WISE_CAT = True
  - ENABLE_ANXIOUS_CAT = True
  - ENABLE_DIVA_CAT = True
```

5. Click **Apply**

## Configuration

### Required Settings

#### 1. Network Configuration

**Find Your Unraid IP:**
```bash
# In Unraid console
ip addr show br0 | grep inet
# Example: 192.168.1.100
```

**Router Port Forwarding:**

Forward these ports to your Unraid IP:
- **5060 UDP** → Asterisk SIP
- **10000-10099 UDP** → Asterisk RTP

#### 2. SIP Provider Setup

**Edit Asterisk SIP Configuration:**
```bash
nano /mnt/user/appdata/meow-now/asterisk/config/sip.conf
```

**Update these lines:**
```ini
[general]
; Your Unraid's public IP (from curl ifconfig.me)
externip=YOUR_PUBLIC_IP

; Your local network
localnet=192.168.1.0/255.255.255.0

[numberbarn]
; Your SIP provider credentials
username=YOUR_NUMBERBARN_USERNAME
secret=YOUR_NUMBERBARN_PASSWORD
host=sip.numberbarn.com
```

**Update DID Number:**
```bash
nano /mnt/user/appdata/meow-now/asterisk/config/extensions.conf
```

```ini
[from-numberbarn]
; Replace with your actual phone number
exten => 15551234567,1,NoOp(Meow-Now call)
```

#### 3. Phone Number Forwarding

**NumberBarn Setup:**
1. Log into [NumberBarn](https://www.numberbarn.com/)
2. Select your phone number
3. Go to Call Forwarding settings
4. Set forwarding type: **SIP URI**
5. Enter: `sip:YOUR_PUBLIC_IP:5060`
6. Enable forwarding
7. Save

### Optional Settings

#### Ollama Integration (for Dynamic Cat Responses)

If you have Ollama running on your Unraid server or network:

**In Unraid Docker settings, add:**
```
OLLAMA_URL = http://192.168.1.100:11434
```

**Or edit .env:**
```bash
OLLAMA_URL=http://192.168.1.100:11434
```

#### TTS Engine Selection

**espeak-ng (default, included):**
- Fast, lightweight
- Robotic but clear
- No additional setup

**Piper TTS (better quality):**
- More natural voice
- Requires model download (done automatically)
- Slightly slower

```bash
TTS_ENGINE=piper
```

## Post-Installation

### 1. Generate Audio Files

```bash
# SSH into Unraid or use console
docker exec -it meow-now python scripts/setup_audio.py
```

This generates:
- 5 realistic cat meow samples
- 7 IVR audio prompts

### 2. Verify Installation

**Check Web Interface:**
```
http://YOUR_UNRAID_IP:5000
```

**Check Logs:**
```bash
docker logs meow-now
docker logs meow-asterisk
```

**Check SIP Registration:**
```bash
docker exec -it meow-asterisk asterisk -rx "sip show registry"
# Should show: "Registered"
```

### 3. Test Call

1. Call your phone number from a mobile phone
2. You should hear: "Welcome to Meow Now!"
3. Press **1** for Meow Mockery
4. Or press **2** for Talkative Cats

## Unraid-Specific Features

### Integration with Unraid Dashboard

**Access from Dashboard:**
- Container will appear in Docker tab
- Click **WebUI** icon to open status page
- View logs directly from Docker tab

### Network Isolation

**If using custom networks:**
```bash
# In docker-compose.unraid.yml
networks:
  meow-network:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.0.0/16
```

### GPU Acceleration (Optional)

If you have a GPU for TTS:
```yaml
services:
  meow-now:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

### Automatic Updates

**Using Watchtower:**
```bash
docker run -d \
  --name watchtower \
  -v /var/run/docker.sock:/var/run/docker.sock \
  containrrr/watchtower \
  --interval 3600 \
  meow-now meow-asterisk
```

## Troubleshooting

### Container Won't Start

**Check logs:**
```bash
docker logs meow-now --tail 50
```

**Common issues:**
- Missing audio directories
- Permission issues
- Port conflicts

**Fix:**
```bash
# Recreate directories
mkdir -p /mnt/user/appdata/meow-now/{audio,logs,models}
chmod -R 755 /mnt/user/appdata/meow-now

# Restart container
docker restart meow-now
```

### No Incoming Calls

**Verify ports are open:**
```bash
# From Unraid console
netstat -ulnp | grep -E "(5060|10000)"
```

**Check SIP registration:**
```bash
docker exec -it meow-asterisk asterisk -rvvv
# Then in Asterisk CLI:
sip show registry
sip show peers
```

### One-Way Audio

**Check NAT settings:**
```bash
nano /mnt/user/appdata/meow-now/asterisk/config/sip.conf
```

Ensure:
```ini
externip=YOUR_PUBLIC_IP
nat=force_rport,comedia
```

### Meows Don't Sound Real

**Regenerate audio:**
```bash
docker exec -it meow-now bash
cd audio/meow_samples
rm *.wav
python scripts/setup_audio.py
```

## Performance Tuning

### CPU Priority

**In Unraid Docker settings:**
```
CPU Pinning: 0,1  (assign specific cores)
CPU Shares: 2048  (higher priority)
```

### Memory Limits

```yaml
services:
  meow-now:
    mem_limit: 512m
    mem_reservation: 256m
```

### Audio Buffer Tuning

**For lower latency:**
```bash
# In .env
AUDIO_BUFFER_SIZE=512
```

## Backup & Restore

### Backup Configuration

```bash
# Create backup
tar czf meow-now-backup-$(date +%Y%m%d).tar.gz \
  /mnt/user/appdata/meow-now/*.env \
  /mnt/user/appdata/meow-now/asterisk/config

# Store in Unraid backup location
mv meow-now-backup-*.tar.gz /mnt/user/backups/
```

### Restore

```bash
# Extract backup
cd /mnt/user/appdata/meow-now
tar xzf /mnt/user/backups/meow-now-backup-YYYYMMDD.tar.gz

# Restart containers
docker-compose restart
```

## Unraid Community App Submission

**To submit to Community Applications:**

1. Template is available at: `unraid-template.xml`
2. Icon available at: `docs/icon.png`
3. Repository: `ghcr.io/quadseven/meow-now`

**For CA admins:**
- Category: Tools
- Support thread: GitHub Issues
- Minimum Unraid version: 6.9.0

## Updates & Maintenance

### Update Container

**Method 1: Unraid UI**
1. Go to Docker tab
2. Click **Check for Updates**
3. Click **Update** next to MeowNow

**Method 2: Command Line**
```bash
cd /mnt/user/appdata/meow-now
docker-compose pull
docker-compose up -d
```

### Maintenance Schedule

**Weekly:**
- Check logs for errors
- Verify SIP registration
- Test a call

**Monthly:**
- Clean old recordings: `rm /mnt/user/appdata/meow-now/audio/recordings/*`
- Review disk usage
- Update containers

## Support

**Documentation:**
- Main README: https://github.com/quadseven/meow-now
- Deployment Guide: DEPLOYMENT.md
- Architecture: ARCHITECTURE.md

**Get Help:**
- GitHub Issues: https://github.com/quadseven/meow-now/issues
- Unraid Forums: Coming soon

**Logs Location:**
```bash
/mnt/user/appdata/meow-now/logs/meow-now.log
docker logs meow-now
docker logs meow-asterisk
```

---

**Happy meowing on Unraid! 🐱**
