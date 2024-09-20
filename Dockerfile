# FROM pytorch/pytorch:2.1.0-cuda11.8-cudnn8-runtime
FROM pytorch/pytorch:1.13.1-cuda11.6-cudnn8-runtime
## Note that there is not a PyTorch 2.0.1 image with CUDA 11.8

LABEL authors="Colby T. Ford <colby@tuple.xyz>"

## Set environment variables
ENV MPLCONFIGDIR /data/MPL_Config
ENV TORCH_HOME /data/Torch_Home
ENV TORCH_EXTENSIONS_DIR /data/Torch_Extensions
ENV DEBIAN_FRONTEND noninteractive

## Install system requirements
RUN apt update && \
    apt-get install -y --reinstall \
        ca-certificates && \
    apt install -y \
        git \
        vim \
        wget \
        libxml2 \
        libgl-dev \
        libgl1

## Make directories
RUN mkdir -p /software/
WORKDIR /software/

## Install dependencies from Conda/Mamba
COPY environment.yml /software/environment.yml
RUN conda env create -f environment.yml
RUN conda init bash && \
    echo "conda activate flow" >> ~/.bashrc
SHELL ["/bin/bash", "--login", "-c"]

## Install PepFlowww
RUN git clone https://github.com/Ced3-han/PepFlowww && \
    cd PepFlowww && \
    pip install -e .
WORKDIR /software/PepFlowww/

CMD /bin/bash