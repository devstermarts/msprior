import logging
import os

import gin
import pytorch_lightning as pl
import torch
from absl import app, flags
from pytorch_lightning import callbacks
from torch.utils import data

from msprior.attention import Prior
from msprior.dataset import SequenceDataset

FLAGS = flags.FLAGS
flags.DEFINE_multi_string("config",
                          default="msprior/configs/decoder_only.gin",
                          help="config to parse.")
flags.DEFINE_string("db_path",
                    default=None,
                    required=True,
                    help="path to dataset.")
flags.DEFINE_integer("val_size",
                     default=8192,
                     help="size of validation dataset.")
flags.DEFINE_integer("batch_size", default=64, help="batch size.")
flags.DEFINE_string("name", default=None, required=True, help="train name.")
flags.DEFINE_integer("gpu", default=0, help="gpu index.")
flags.DEFINE_integer("workers",
                     default=0,
                     help="num workers during data loading.")
flags.DEFINE_string("pretrained_embedding",
                    default=None,
                    help="use pretrained embeddings from rave.")
flags.DEFINE_multi_string("override",
                          default=[],
                          help="additional gin bindings.")
flags.DEFINE_string("ckpt",
                    default=None,
                    help="checkpoint to resume training from.")


def add_ext(config: str):
    if config[-4:] != ".gin":
        config += ".gin"
    return config


def main(argv):
    logging.info("parsing configuration")
    configs = list(map(add_ext, FLAGS.config))

    gin.parse_config_files_and_bindings(
        configs,
        [f"PRETRAINED_RAVE='{FLAGS.pretrained_embedding}'"] + FLAGS.override,
    )

    logging.info("building model")
    model = Prior()

    logging.info("loading dataset")
    dataset = SequenceDataset(db_path=FLAGS.db_path)
    train, val = data.random_split(
        dataset,
        (len(dataset) - FLAGS.val_size, FLAGS.val_size),
        generator=torch.Generator().manual_seed(42),
    )
    train_loader = data.DataLoader(
        train,
        batch_size=FLAGS.batch_size,
        shuffle=True,
        drop_last=True,
        num_workers=FLAGS.workers,
    )
    val_loader = data.DataLoader(
        val,
        batch_size=FLAGS.batch_size,
        shuffle=False,
        drop_last=False,
        num_workers=FLAGS.workers,
    )

    os.makedirs(os.path.join("runs", FLAGS.name), exist_ok=False)
    with open(os.path.join("runs", FLAGS.name, "config.gin"),
              "w") as config_out:
        config_out.write(gin.config_str())

    logging.info("creating trainer")
    trainer = pl.Trainer(
        logger=pl.loggers.TensorBoardLogger('runs', name=FLAGS.name),
        accelerator='gpu',
        devices=[FLAGS.gpu],
        callbacks=[
            callbacks.LearningRateMonitor(logging_interval='step'),
            callbacks.ModelCheckpoint(monitor="val_cross_entropy",
                                      filename='best'),
            callbacks.ModelCheckpoint(filename='last'),
            callbacks.EarlyStopping(
                "val_cross_entropy",
                patience=10,
            )
        ],
        log_every_n_steps=10,
        val_check_interval=1000,
    )

    torch.backends.cudnn.benchmark = True
    torch.set_float32_matmul_precision('high')

    logging.info("launch training")
    trainer.fit(
        model,
        train_loader,
        val_loader,
        ckpt_path=FLAGS.ckpt,
    )


if __name__ == "__main__":
    app.run(main)