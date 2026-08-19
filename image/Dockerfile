# Reusable "server" image: a stock Ubuntu that runs an SSH daemon for root
# (key auth) and is otherwise a normal, fully-apt-able OS. Every podjump
# server container is started from this image unless you override the image.
FROM ubuntu:latest

ENV DEBIAN_FRONTEND=noninteractive \
    PAM_MODULES=libpam-runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        openssh-server \
        ca-certificates \
        curl \
        sudo \
        net-tools \
        iproute2 \
        iputils-ping \
        openssh-client \
        less \
        vim-tiny \
        htop \
    && rm -rf /var/lib/apt/lists/*

# sshd needs a /run/sshd privilege-separation dir at start time (the
# entrypoint creates it if missing). Pre-create to be safe.
RUN mkdir -p /run/sshd

# Preconfigure sshd: root key login, host keys generated at build time.
RUN ssh-keygen -A \
    && sed -i \
        -e 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' \
        -e 's/^#\?PubkeyAuthentication.*/PubkeyAuthentication yes/' \
        -e 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' \
        -e 's/^#\?UsePAM.*/UsePAM yes/' \
        /etc/ssh/sshd_config

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 22
ENTRYPOINT ["/entrypoint.sh"]
