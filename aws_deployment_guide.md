# ☁️ Deploying GlassEntials CRM to AWS EC2

This guide covers deploying your Flask application on an **AWS EC2 Ubuntu server** using **Gunicorn** (application server) and **Nginx** (reverse proxy server). 

---

## Phase 1: Server Setup (AWS Console)

1. **Launch an EC2 Instance**:
   - Go to the AWS EC2 Dashboard and click **Launch Instance**.
   - **OS**: Ubuntu Server 24.04 LTS (Free Tier eligible).
   - **Instance Type**: `t2.micro` or `t3.micro`.
   - **Key Pair**: Create a new `.pem` key pair or use an existing one (needed for SSH).
   - **Network Settings**: Allow **HTTP (80)**, **HTTPS (443)**, and **SSH (22)** traffic.

2. **Connect to your Instance**:
   Open your local terminal and SSH into your server:
   ```bash
   ssh -i /path/to/your-key.pem ubuntu@your_ec2_public_ip
   ```

---

## Phase 2: Installing Dependencies

Once connected to your Ubuntu server, update it and install required system packages:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip python3-venv python3-dev build-essential libmysqlclient-dev pkg-config nginx git -y
```
*(Note: `libmysqlclient-dev` and `pkg-config` are required for `mysqlclient`/`pymysql` to compile correctly on Linux).*

---

## Phase 3: Application Setup

1. **Clone Your Repository**:
   *(Assuming your code is on GitHub. If not, use `scp` to copy files from your local machine to the server).*
   ```bash
   git clone https://github.com/yourusername/CRM-GlassEntials.git
   cd CRM-GlassEntials
   ```

2. **Create the Virtual Environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install Python Packages**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Create the Production Environment File**:
   Copy the example template and edit it with your production database credentials (e.g., an AWS RDS instance or a MySQL server you install on this EC2):
   ```bash
   cp .env.example .env
   nano .env
   ```
   *Press `Ctrl+X`, then `Y`, then `Enter` to save and exit `nano`.*

5. **Initialize the Database**:
   Run your migrations to create the tables in the production database:
   ```bash
   flask db upgrade
   ```

---

## Phase 4: Configure Gunicorn (Systemd Service)

We need Gunicorn to run endlessly in the background. We do this by creating a `systemd` service.

1. **Create a Service File**:
   ```bash
   sudo nano /etc/systemd/system/glassentials.service
   ```

2. **Paste the following configuration**:
   *(Make sure to replace `ubuntu` if you used a different user, and verify the path if your cloned folder name is different).*
   ```ini
   [Unit]
   Description=Gunicorn daemon for GlassEntials CRM
   After=network.target

   [Service]
   User=ubuntu
   Group=www-data
   WorkingDirectory=/home/ubuntu/CRM-GlassEntials
   Environment="PATH=/home/ubuntu/CRM-GlassEntials/venv/bin"
   ExecStart=/home/ubuntu/CRM-GlassEntials/venv/bin/gunicorn --workers 3 --bind unix:glassentials.sock -m 007 wsgi:app

   [Install]
   WantedBy=multi-user.target
   ```

3. **Start and Enable the Service**:
   ```bash
   sudo systemctl start glassentials
   sudo systemctl enable glassentials
   ```
   *(Check the status with `sudo systemctl status glassentials` to ensure it is "active (running)").*

---

## Phase 5: Configure Nginx

Nginx will face the outside world and forward web traffic to Gunicorn via the `.sock` file we just created.

1. **Create an Nginx Server Block**:
   ```bash
   sudo nano /etc/nginx/sites-available/glassentials
   ```

2. **Paste the following block**:
   *(Replace `your_domain_or_IP` with your EC2 Public IPv4 address or your actual domain name).*
   ```nginx
   server {
       listen 80;
       server_name your_domain_or_IP;

       location / {
           include proxy_params;
           proxy_pass http://unix:/home/ubuntu/CRM-GlassEntials/glassentials.sock;
       }

       location /static {
           alias /home/ubuntu/CRM-GlassEntials/static;
           client_max_body_size 5M; # Limit file uploads to 5MB (Profile Pics)
       }
   }
   ```

3. **Enable the Configuration and Restart**:
   ```bash
   sudo ln -s /etc/nginx/sites-available/glassentials /etc/nginx/sites-enabled
   sudo nginx -t     # This checks for syntax errors
   sudo systemctl restart nginx
   ```

---

## 🎉 Phase 6: You're Live!

If you navigate to your EC2 instance's Public IP address in your browser, your **GlassEntials CRM** should now be fully live!

### 🔒 Bonus: Securing with HTTPS (SSL)
If you point a domain name (like `crm.yourcompany.com`) to your EC2 IP Address via Route53 or your DNS provider, you can install a free SSL certificate instantly:

```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d crm.yourcompany.com
```
