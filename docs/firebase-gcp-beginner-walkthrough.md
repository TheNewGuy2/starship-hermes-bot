# Firebase + Google Cloud beginner walkthrough

This guide is the plain-English setup path for the stable Hermes/Pine Bridge deployment.

The goal is:

- Firebase project: the umbrella Google project and optional public landing page.
- Firebase Hosting: a simple public page like `www.example.com`.
- Google Cloud Compute Engine VM: the real Python app that receives TradingView alerts and talks to E*TRADE.
- Stable webhook: `https://bot.example.com/webhooks/tradingview`.

## The important mental model

There are four separate pieces that are easy to blur together:

1. Domain registrar
   - This is where you buy the domain name, like `example.com`.
   - Firebase does not really replace this. If you do not already own a domain, buy one from a registrar such as Squarespace, Cloudflare, Namecheap, etc.

2. DNS
   - DNS is the address book for the domain.
   - It decides where each subdomain goes.
   - Example:
     - `www.example.com` goes to Firebase Hosting.
     - `bot.example.com` goes to the Google Cloud VM.

3. Firebase Hosting
   - Great for a simple website, landing page, docs page, or dashboard launcher.
   - It gives easy HTTPS and a Firebase URL like `your-project.web.app`.
   - We should not run the current Python bridge directly on Firebase Hosting because the bridge is file-backed and needs durable local state.

4. Google Cloud VM
   - This is the actual always-on server.
   - It runs the Python web app in Docker.
   - It stores E*TRADE session state, tickets, Pine bridge state, logs, and audit files.

## Recommended domain layout

Use one domain with two subdomains:

```text
www.example.com
```

Public Firebase-hosted page. Optional but nice.

```text
bot.example.com
```

Private Starship/Hermes app on the Google Cloud VM. TradingView sends alerts here.

The TradingView webhook URL should be:

```text
https://bot.example.com/webhooks/tradingview
```

Do not point TradingView at the Firebase `www` page.

## Phase 1 - Create the Firebase / Google project

Use the Firebase Console:

```text
https://console.firebase.google.com/
```

Steps:

1. Click `Add project`.
2. Name it something like `starship-hermes`.
3. Google Analytics can be off for now.
4. After the project is created, open the project settings and note the project ID.
5. Add billing / upgrade to Blaze if needed. The VM requires Google Cloud billing.

You now have a Firebase project and a matching Google Cloud project.

## Phase 2 - Buy or choose a domain

If you already own a domain, use it.

If not, buy one from a domain registrar. A simple example:

```text
your-hermes-domain.com
```

For this guide, replace:

```text
example.com
```

with your real domain.

Recommended subdomains:

```text
www.example.com
bot.example.com
```

## Phase 3 - Create the Google Cloud VM from Cloud Shell

Open Google Cloud Shell:

```text
https://console.cloud.google.com/
```

Click the terminal icon in the top-right.

Clone this repo into Cloud Shell:

```bash
git clone https://github.com/TheNewGuy2/starship-hermes-bot.git
cd starship-hermes-bot
```

Run the bootstrap script, replacing only `PROJECT_ID`.

```bash
export PROJECT_ID="your-google-project-id"
bash deploy/gcp/bootstrap-stable-vm.sh
```

The script creates:

- required Google Cloud APIs
- permanent static IP
- HTTP/HTTPS firewall rule
- Ubuntu Compute Engine VM

At the end it prints the static IP. Save it.

## Phase 4 - Point `bot.example.com` to the VM

Go to your domain registrar's DNS settings.

Create this DNS record:

```text
Type: A
Name/Host: bot
Value/Address: the STATIC_IP from Cloud Shell
TTL: Auto or 300
```

Example:

```text
bot.example.com -> 34.123.45.67
```

Wait a few minutes. Sometimes DNS takes longer.

## Phase 5 - Install the app on the VM

In Google Cloud Shell, SSH into the VM:

```bash
gcloud compute ssh starship-hermes-web --zone us-west1-a
```

Now you are inside the VM.

Run the VM app setup script directly from GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/TheNewGuy2/starship-hermes-bot/main/deploy/gcp/setup-vm-app.sh -o /tmp/setup-vm-app.sh
bash /tmp/setup-vm-app.sh
```

If your DNS record for `bot.example.com` is already pointed to the VM static IP, run:

```bash
BOT_DOMAIN="bot.example.com" bash /tmp/setup-vm-app.sh
```

Replace `bot.example.com` with your real bot subdomain.

## Phase 6 - Add secrets privately on the VM

Still inside the VM, paste this helper:

```bash
cd /opt/starship-alpha/app

write_secret () {
  local name="$1"
  local prompt="$2"
  local value
  read -rsp "$prompt: " value
  echo
  printf '%s' "$value" | sudo tee "deploy/env/secrets/$name" >/dev/null
  sudo chmod 600 "deploy/env/secrets/$name"
}
```

Now run these one by one.

Do not paste these secret values into chat.

```bash
write_secret STARSHIP_ADMIN_USER "Admin username"
write_secret STARSHIP_ADMIN_PASSWORD "Admin password"
write_secret TRADINGVIEW_WEBHOOK_SECRET "TradingView webhook secret"
write_secret ETRADE_CONSUMER_KEY "E*TRADE consumer key"
write_secret ETRADE_CONSUMER_SECRET "E*TRADE consumer secret"
write_secret ETRADE_DEFAULT_ACCOUNT_ID_KEY "E*TRADE account ID key"
write_secret SLACK_WEBHOOK_URL "Slack webhook URL, or press Enter if blank"
```

Generate the internal engine secret:

```bash
openssl rand -hex 24 | sudo tee deploy/env/secrets/ENGINE_INGEST_SECRET >/dev/null
sudo chmod 600 deploy/env/secrets/ENGINE_INGEST_SECRET
```

## Phase 7 - Start the Python app

Still inside the VM:

```bash
cd /opt/starship-alpha/app
sudo docker compose -f deploy/docker/docker-compose.gcp.yml up -d --build starship-web
sudo docker compose -f deploy/docker/docker-compose.gcp.yml ps
curl http://127.0.0.1:8000/health
```

Expected result:

```json
{"ok":true}
```

## Phase 8 - Put HTTPS in front with Caddy

Replace `bot.example.com` with your real bot subdomain:

```bash
export BOT_DOMAIN="bot.example.com"

sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
$BOT_DOMAIN {
    reverse_proxy 127.0.0.1:8000
}
EOF

sudo systemctl reload caddy
curl "https://$BOT_DOMAIN/health"
```

Expected result:

```json
{"ok":true}
```

If this fails immediately after DNS changes, wait a few minutes and retry.

## Phase 9 - Connect E*TRADE

Open this in your browser:

```text
https://bot.example.com/broker/etrade
```

Steps:

1. Click the E*TRADE auth/start button.
2. Log in to E*TRADE.
3. Copy the verifier code.
4. Paste it back into the hosted Starship page.
5. Load accounts.
6. Confirm the account ID key matches the one in `ETRADE_DEFAULT_ACCOUNT_ID_KEY`.

The E*TRADE token is saved on the VM in:

```text
/opt/starship-alpha/app/data/etrade_session.json
```

## Phase 10 - Create Firebase Hosting for the public page

This is optional but matches the Firebase plan.

In Firebase Console:

```text
Build -> Hosting -> Get started
```

You can use Firebase Hosting for:

- a public landing page
- links to the private bot page
- notes for yourself

Connect:

```text
www.example.com
```

to Firebase Hosting.

Firebase will show you DNS records to add. Add exactly what Firebase gives you.

Do not connect `bot.example.com` to Firebase Hosting. `bot.example.com` must point to the VM.

## Phase 11 - Update TradingView

In TradingView alert settings:

```text
Webhook URL:
https://bot.example.com/webhooks/tradingview
```

The alert JSON must include:

```json
{
  "secret": "same value as TRADINGVIEW_WEBHOOK_SECRET"
}
```

Keep using the Pine script alert payload we patched earlier.

## Phase 12 - Daily hosted workflow

Each morning:

1. Open `https://bot.example.com/health`.
2. Open `https://bot.example.com/broker/etrade`.
3. Re-auth E*TRADE if the token expired.
4. Open `https://bot.example.com/pine-bridge`.
5. Confirm TradingView webhook is still `https://bot.example.com/webhooks/tradingview`.
6. Watch `/pine-bridge` and `/tickets` during the day.

## Phase 13 - GitHub Actions later

After the VM works manually, we wire GitHub Actions.

That will let this happen automatically:

```text
push to main -> CI passes -> GitHub SSHes into VM -> VM pulls latest code -> Docker restarts app
```

Do not set up GitHub Actions first. Get the manual VM healthy first. It is much easier to debug in this order.
