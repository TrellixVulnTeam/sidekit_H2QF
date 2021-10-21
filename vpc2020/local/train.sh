#!/bin/bash

export NUM_NODES=1
export NUM_GPUS_PER_NODE=2
export NODE_RANK=0
export WORLD_SIZE=$(($NUM_NODES * $NUM_GPUS_PER_NODE))

# env DEV4S=True WORLD_SIZE=1 ipython3 train_xtractor.py # DEV

mkdir -p model log
cd ../..
. ./env.sh
cd -

python3 -m torch.distributed.launch \
       --nproc_per_node=$NUM_GPUS_PER_NODE \
       --nnodes=$NUM_NODES \
       --node_rank $NODE_RANK \
       train_xtractor.py