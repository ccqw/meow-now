# Meow-Now Setup Guide

Complete setup instructions for deploying Meow-Now on Unraid with NumberBarn.

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [NumberBarn Configuration](#numberbarn-configuration)
3. [Unraid Docker Setup](#unraid-docker-setup)
4. [Asterisk Configuration](#asterisk-configuration)
5. [Ollama Integration](#ollama-integration)
6. [Audio Prompts](#audio-prompts)
7. [Testing](#testing)
8. [Troubleshooting](#troubleshooting)

## Prerequisites

### What You Need
- Unraid server with Docker support
- Phone number with NumberBarn (or any SIP provider)
- Static IP or Dynamic DNS for your home network
- Port forwarding on your router
- (Optional) Ollama running on local network/Tailscale

### Network Requirements
- Port 5060 UDP - SIP signaling
- Port 10000-10099 UDP - RTP (voice media)
- Port 5000 TCP - Web interface (optional, internal only)
- Port 4573 TCP - AGI server (internal only)

## NumberBarn Configuration

### Step 1: Get SIP Credentials

1. Log into your NumberBarn account
2. Go to **Phone Numbers** → Select your number
3. Click **Settings** → **SIP Details**
4. Note down:
   - SIP Server: `sip.numberbarn.com` (or similar)
   - Username
   - Password
   - Your phone number (DID)

### Step 2: Configure SIP Forwarding

1. In NumberBarn portal, set call forwarding:
   - Forward to: **SIP Address**
   - Enter your external IP or DDNS hostname
   - Port: `5060`

2. Enable SIP authentication (if available)

## Unraid Docker Setup

### Method 1: Using Docker Compose (Recommended)

1. **Install Docker Compose on Unraid**
   ```bash
   # SSH into your Unraid server
   # Install docker-compose via pip or use docker-compose plugin
   ```

2. **Clone Repository**
   ```bash
   cd /mnt/user/appdata
   git clone https://github.com/yourusername/meow-now.git
   cd meow-now
   ```

3. **Configure Environment**
   ```bash
   cp .env.example .env
   nano .env
   ```

   Update these critical values:
   ```bash
   # Asterisk/SIP
   ASTERISK_HOST=meow-asterisk  # Docker container name

   # Ollama (if using)
   OLLAMA_URL=http://your-tailscale-hostname:11434

   # Audio settings
   SAMPLE_RATE=8000

   # Enable cat personalities
   ENABLE_GRUMPY_CAT=True
   ENABLE_WISE_CAT=True
   ENABLE_ANXIOUS_CAT=True
   ENABLE_DIVA_CAT=True
   ```

4. **Build and Start**
   ```bash
   docker-compose up -d
   ```

5. **Check Logs**
   ```bash
   docker-compose logs -f meow-now
   docker-compose logs -f asterisk
   ```

### Method 2: Unraid Community Applications

1. **Create Docker Container Manually**
   - In Unraid UI, go to **Docker** tab
   - Click **Add Container**

2. **Configure Container**
   - **Name**: `meow-now`
   - **Repository**: Build from Dockerfile or use pre-built image
   - **Network Type**: `bridge`

3. **Port Mappings**
   - Container Port `5000` → Host Port `5000` (TCP)
   - Container Port `4573` → Host Port `4573` (TCP)

4. **Volume Mappings**
   - `/mnt/user/appdata/meow-now/audio` → `/app/audio`
   - `/mnt/user/appdata/meow-now/logs` → `/app/logs`
   - `/mnt/user/appdata/meow-now/models` → `/app/models`

5. **Environment Variables**
   - Add all variables from `.env.example`

## Asterisk Configuration

### Step 1: Edit SIP Configuration

Edit `config/asterisk/sip.conf`:

```ini
[numberbarn-trunk]
type=friend
host=sip.numberbarn.com
fromdomain=sip.numberbarn.com
context=from-numberbarn
dtmfmode=rfc2833
disallow=all
allow=ulaw
allow=alaw
insecure=port,invite
nat=force_rport,comedia

username=YOUR_NUMBERBARN_USERNAME
secret=YOUR_NUMBERBARN_PASSWORD
fromuser=YOUR_NUMBERBARN_USERNAME

register => YOUR_NUMBERBARN_USERNAME:YOUR_NUMBERBARN_PASSWORD@sip.numberbarn.com/YOUR_PHONE_NUMBER
```

**Important**: Replace:
- `YOUR_NUMBERBARN_USERNAME`
- `YOUR_NUMBERBARN_PASSWORD`
- `YOUR_PHONE_NUMBER` (e.g., 15551234567)

### Step 2: Set Your External IP

In `sip.conf`, set your external IP:

```ini
[general]
externip=YOUR_PUBLIC_IP_HERE
localnet=192.168.0.0/255.255.0.0  ; Adjust to your local network ; leak-guard-allow: documentation example for local network configuration
```

Get your public IP: `curl ifconfig.me`

Or use DDNS: `externhost=yourname.ddns.net`

### Step 3: Configure Extensions

Edit `config/asterisk/extensions.conf`:

Replace the example DID with your actual number:

```ini
[from-numberbarn]
exten => YOUR_PHONE_NUMBER,1,NoOp(Meow-Now call from ${CALLERID(num)})
 same => n,Goto(from-trunk,${EXTEN},1)
```

### Step 4: Restart Asterisk

```bash
docker-compose restart asterisk
```

Or in Asterisk CLI:
```bash
docker exec -it meow-asterisk asterisk -rvvv
CLI> sip reload
CLI> dialplan reload
CLI> sip show registry
```

## Router Configuration

### Port Forwarding Rules

Configure these rules on your router:

| Service | External Port | Internal Port | Protocol | Internal IP |
|---------|--------------|---------------|----------|-------------|
| SIP     | 5060         | 5060          | UDP      | Unraid IP   |
| RTP     | 10000-10099  | 10000-10099   | UDP      | Unraid IP   |

### Firewall Rules

Ensure your firewall allows:
- Inbound UDP 5060 (SIP)
- Inbound UDP 10000-10099 (RTP)

## Ollama Integration

If you're running Ollama over Tailscale for dynamic cat personalities:

### Step 1: Find Ollama Hostname

```bash
# On machine running Ollama
tailscale status
# Note the hostname (e.g., my-ollama-machine)
```

### Step 2: Test Connection

```bash
# From Unraid
curl http://my-ollama-machine:11434/api/tags
```

### Step 3: Set Environment Variable

In `.env`:
```bash
OLLAMA_URL=http://my-ollama-machine:11434
```

### Step 4: Pull Model

On your Ollama machine:
```bash
ollama pull llama2
# or
ollama pull llama3
# or any other conversational model
```

### Fallback Behavior

If Ollama is unavailable, the system will use pre-written cat monologues.

## Audio Prompts

### Required Audio Files

Create these files in `audio/prompts/` (8kHz, mono, WAV):

- `main_menu.wav` - Welcome message and menu options
- `welcome.wav` - Initial greeting
- `menu_prompt.wav` - "Press 1 for... Press 2 for..."
- `meow_instructions.wav` - Instructions for meow mockery
- `meow_goodbye.wav` - Goodbye after meow experience
- `cats_intro.wav` - Introduction to talkative cats
- `goodbye.wav` - General goodbye message
- `error.wav` - Error message

### Creating Audio Files

**Option 1: Text-to-Speech**

Use a service like [TTSMaker](https://ttsmaker.com/) or local TTS:

```bash
# Example with Piper
echo "Welcome to Meow Now!" | piper \
  --model models/piper/en_US-lessac-medium.onnx \
  --output_file audio/prompts/welcome.wav
```

**Option 2: Record Your Own**

Use Audacity or similar:
1. Record at 44.1kHz
2. Export as WAV
3. Convert to 8kHz mono:

```bash
sox input.wav -r 8000 -c 1 output.wav
```

**Option 3: Use Asterisk Sound Library**

Download free prompts from Asterisk:
```bash
# Asterisk has built-in sounds
# Or use: https://www.asterisksounds.org/
```

### Example Script for Audio Prompts

`audio/prompts/example-scripts.txt`:

```
welcome.wav:
"Welcome to Meow Now, the premier cat-based voice experience!"

main_menu.wav:
"Press 1 to have your voice mocked by a cat. Press 2 to hear from our talkative feline friends."

meow_instructions.wav:
"Get ready to be mocked! After the beep, start talking. You have 60 seconds. Press pound when finished."

meow_goodbye.wav:
"Thanks for letting us mock you! Meow!"

cats_intro.wav:
"Connecting you to one of our talkative cats..."
```

## Testing

### Test 1: Web Interface

Visit: `http://unraid-ip:5000`

Should show status page with green "Running" indicator.

### Test 2: Health Check

```bash
curl http://unraid-ip:5000/health
```

Should return JSON with status information.

### Test 3: Asterisk CLI

```bash
docker exec -it meow-asterisk asterisk -rvvv

# Check SIP registration
CLI> sip show registry

# Should show: numberbarn-trunk Registered

# Check peers
CLI> sip show peers

# Make test call from CLI
CLI> console dial 100@internal
```

### Test 4: External Call

Call your NumberBarn number from your phone. You should:
1. Hear welcome message
2. Be prompted to press 1 or 2
3. Experience either meow mockery or talkative cat

### Test 5: Check Logs

```bash
# Application logs
tail -f logs/meow-now.log

# Docker logs
docker-compose logs -f

# Asterisk logs
docker exec -it meow-asterisk tail -f /var/log/asterisk/full
```

## Troubleshooting

### No Incoming Calls

**Problem**: Calls don't reach Asterisk

**Solutions**:
1. Check port forwarding: `netstat -tulpn | grep 5060`
2. Verify SIP registration: `sip show registry` in Asterisk CLI
3. Check firewall rules
4. Verify external IP in `sip.conf` matches your public IP
5. Test with SIP testing tools: [SIP Test](https://www.voip-info.org/sip-test/)

### Call Connects But No Audio

**Problem**: Call answers but silence

**Solutions**:
1. Check RTP ports (10000-10099) are forwarded
2. Verify NAT settings in `sip.conf`:
   ```ini
   nat=force_rport,comedia
   ```
3. Check codec compatibility: `allow=ulaw` in `sip.conf`
4. Enable RTP debugging:
   ```
   CLI> rtp set debug on
   ```

### AGI Connection Failed

**Problem**: Asterisk can't connect to AGI server

**Solutions**:
1. Check meow-now container is running: `docker ps`
2. Verify AGI server started: `docker logs meow-now | grep AGI`
3. Test AGI connection from Asterisk container:
   ```bash
   docker exec -it meow-asterisk telnet meow-now 4573
   ```
4. Check Docker network: both containers in same network

### Audio Quality Issues

**Problem**: Robotic/choppy audio

**Solutions**:
1. Ensure correct sample rate (8000 Hz):
   ```bash
   file audio/prompts/welcome.wav
   ```
2. Check codec: use ulaw or alaw (not gsm for quality)
3. Verify sufficient bandwidth for RTP
4. Check CPU usage during calls

### TTS Not Working

**Problem**: Cat personalities don't speak

**Solutions**:
1. Check Piper model exists: `ls models/piper/`
2. Verify Piper installation: `piper --version`
3. Test TTS manually:
   ```bash
   echo "Test" | piper --model models/piper/en_US-lessac-medium.onnx --output_file test.wav
   ```
4. Check logs for TTS errors: `grep TTS logs/meow-now.log`

### Ollama Not Connecting

**Problem**: Static cat responses instead of dynamic LLM

**Solutions**:
1. Verify Ollama URL: `curl $OLLAMA_URL/api/tags`
2. Check Tailscale connection: `tailscale status`
3. Test from container:
   ```bash
   docker exec -it meow-now curl http://ollama-host:11434/api/tags
   ```
4. Verify model is pulled: `ollama list` on Ollama machine
5. Check for timeout issues (increase in code if needed)

### Meow Mockery Not Working

**Problem**: Recording fails or no meow playback

**Solutions**:
1. Check recording directory permissions: `ls -la audio/recordings/`
2. Verify disk space: `df -h`
3. Check audio analysis logs:
   ```bash
   grep "voice_analyzer" logs/meow-now.log
   ```
4. Test pitch detection with sample audio
5. Ensure `#` key (pound) is detected: check DTMF settings

## Advanced Configuration

### Custom Cat Personalities

Add new personalities in `services/cat_personalities.py`:

```python
class YourCat(CatPersonality):
    def __init__(self):
        super().__init__(
            name="YourCat",
            voice_pitch=1.0,
            speaking_rate=1.0,
            personality_prompt="Your personality description",
            topics=["topic1", "topic2"]
        )
```

Enable in `.env`:
```bash
ENABLE_YOUR_CAT=True
```

### Multiple DIDs

Handle multiple phone numbers in `extensions.conf`:

```ini
[from-numberbarn]
exten => 15551111111,1,Goto(meow-menu,s,1)
exten => 15552222222,1,Goto(different-menu,s,1)
```

### Call Recording

Enable in Asterisk:

```ini
[from-trunk]
exten => _X.,1,NoOp(Incoming call)
 same => n,Answer()
 same => n,MixMonitor(/var/spool/asterisk/monitor/${UNIQUEID}.wav)
 same => n,AGI(agi://${AGI_HOST}:${AGI_PORT})
 same => n,Hangup()
```

## Security Considerations

1. **Change Default Passwords**
   - Update AMI password in `manager.conf`
   - Change Flask SECRET_KEY

2. **Restrict Access**
   - Don't expose port 5000 to internet
   - Use VPN for administrative access
   - Restrict AMI access to local network

3. **SIP Security**
   - Use strong SIP passwords
   - Consider SIP TLS (port 5061)
   - Enable fail2ban for brute force protection

4. **Regular Updates**
   - Keep Asterisk updated
   - Update Python dependencies: `pip install -U -r requirements.txt`

## Performance Tuning

### For Beefy Servers

Increase concurrent calls in `.env`:

```bash
MAX_CONCURRENT_CALLS=10
WORKER_THREADS=4
```

### For Resource-Constrained

```bash
# Use simpler TTS
TTS_ENGINE=piper  # Lighter than Coqui

# Disable Ollama
OLLAMA_URL=

# Reduce audio quality (if needed)
SAMPLE_RATE=8000
```

## Monitoring

### Prometheus Metrics (Optional)

Add monitoring endpoints for production use.

### Log Aggregation

Forward logs to central logging:

```yaml
# In docker-compose.yml
logging:
  driver: "syslog"
  options:
    syslog-address: "udp://your-syslog-server:514"
```

## Getting Help

1. Check logs: `logs/meow-now.log`
2. Enable debug mode: `LOG_LEVEL=DEBUG` in `.env`
3. Test components individually
4. Review Asterisk full log: `/var/log/asterisk/full`

## Next Steps

- Add more cat personalities
- Create custom audio prompts
- Set up monitoring
- Add call analytics
- Integrate with Ollama models
- Create backup strategies
