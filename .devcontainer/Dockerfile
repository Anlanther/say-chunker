FROM mcr.microsoft.com/devcontainers/python:3.14

RUN apt-get update && apt-get install -y \
    zsh \
    git \
    curl \
    zsh-syntax-highlighting \
    && apt-get clean

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
