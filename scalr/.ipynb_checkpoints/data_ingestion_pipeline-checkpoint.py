"""This file is a class for data ingestion into the pipeline."""

from copy import deepcopy
import os
from os import path

import pandas as pd

from scalr.data.preprocess import build_preprocessor
from scalr.data.split import build_splitter
from scalr.utils import FlowLogger
from scalr.utils import read_data
from scalr.utils import write_data


class DataIngestionPipeline:
    """Class for Data Ingestion into the pipeline"""

    def __init__(self, data_config: dict, dirpath: str = '.'):
        """Load data config and create a `data` directory.

        Args:
            data_config (dict): Data processing configuration and paths.
            dirpath (str): Experiment data directory. Defaults to '.'.
        """

        self.flow_logger = FlowLogger('DataIngestion')

        self.data_config = deepcopy(data_config)
        self.target = self.data_config.get('target')
        self.sample_chunksize = self.data_config.get('sample_chunksize')

        # Make some necessary checks and logs.
        if not self.target:
            self.flow_logger.warning('Target not given')

        if not self.sample_chunksize:
            self.flow_logger.warning(
                '''Sample chunk size not given. Will default to not using chunking.
                   Might results in excessive use of memory.''')

        self.datadir = dirpath

    def generate_train_val_test_split(self):
        """A function to split data into train, validation and test sets."""

        # TODO: Move to config validation
        if self.data_config['train_val_test'].get(
                'full_datapath',
                False) and self.data_config['train_val_test'].get(
                    'split_datapaths', False):
            self.flow_logger.warning(
                '''`full_datapath` and `split_datapaths` are both provided in
                the config. Pipeline will use `full_datapath` and it will overwrite
                the `split_datapaths`.
                ''')

        # TODO: Move to config validation
        if self.data_config['train_val_test'].get(
                'feature_subset_datapaths'
        ) or self.data_config['train_val_test'].get('final_datapaths'):
            raise ValueError(
                '''`final_datapaths` or `feature_subset_datapaths` can not be provided
                by the user in the config. These paths are generated by the pipeline!
                ''')

        if self.data_config['train_val_test'].get('full_datapath'):
            self.flow_logger.info('Generating Train, Validation and Test sets')
            if not self.target:
                self.flow_logger.warning(
                    '''Target not provided. Will not be able to perform
                    checks regarding splits.
                    ''')

            full_datapath = self.data_config['train_val_test']['full_datapath']
            self.full_data = read_data(full_datapath)
            splitter_config = deepcopy(
                self.data_config['train_val_test']['splitter_config'])
            splitter, splitter_config = build_splitter(splitter_config)
            self.data_config['train_val_test'][
                'splitter_config'] = splitter_config

            # Make data splits.
            train_val_test_split_indices = splitter.generate_train_val_test_split_indices(
                full_datapath, self.target)

            write_data(train_val_test_split_indices,
                       path.join(self.datadir, 'train_val_test_split.json'))

            # Check data splits.
            if self.target:
                splitter.check_splits(full_datapath,
                                      train_val_test_split_indices, self.target)

            # Write data splits.
            train_val_test_split_dirpath = path.join(self.datadir,
                                                     'train_val_test_split')
            os.makedirs(train_val_test_split_dirpath, exist_ok=True)
            splitter.write_splits(self.full_data, train_val_test_split_indices,
                                  self.sample_chunksize,
                                  train_val_test_split_dirpath)

            # Garbage collection
            del self.full_data

            self.data_config['train_val_test'][
                'split_datapaths'] = train_val_test_split_dirpath

        elif self.data_config['train_val_test'].get('split_datapaths'):
            self.flow_logger.info(
                'Reading Train, Validation and Test sets from config')

        # TODO: Move to config validation
        else:
            raise ValueError(
                'No Data Provided. Please provide `full_datapath` or `split_datapaths`!'
            )

    def preprocess_data(self):
        """A function to apply preprocessing on data splits."""

        self.data_config['train_val_test']['final_datapaths'] = deepcopy(
            self.data_config['train_val_test']['split_datapaths'])

        all_preprocessings = self.data_config.get('preprocess', list())
        if not all_preprocessings:
            return

        self.flow_logger.info('Preprocessing data')
        datapath = self.data_config['train_val_test']['final_datapaths']

        processed_datapath = path.join(self.datadir, 'processed_data')
        os.makedirs(processed_datapath, exist_ok=True)

        for i, (preprocess) in enumerate(all_preprocessings):
            self.flow_logger.info(f'Applying {preprocess["name"]}')

            preprocessor, preprocessor_config = build_preprocessor(
                deepcopy(preprocess))
            self.data_config['preprocess'][i] = preprocessor_config
            # Fit on train data.
            preprocessor.fit(read_data(path.join(datapath, 'train')),
                             self.sample_chunksize)
            # Transform on train, val & test split.
            for split in ['train', 'val', 'test']:
                split_data = read_data(path.join(datapath, split))
                preprocessor.process_data(split_data, self.sample_chunksize,
                                          path.join(processed_datapath, split))

            datapath = processed_datapath

        self.data_config['train_val_test'][
            'final_datapaths'] = processed_datapath

    def generate_mappings(self):
        """A function to generate an Integer mapping to and from target columns."""

        self.flow_logger.info(
            'Generate label mappings for all columns in metadata')

        column_names = read_data(
            path.join(self.data_config['train_val_test']['final_datapaths'],
                      'val')).obs.columns

        data_obs = []
        for split in ['train', 'val', 'test']:
            datapath = path.join(
                self.data_config['train_val_test']['final_datapaths'], split)
            split_data_obs = read_data(datapath).obs
            data_obs.append(split_data_obs)
        full_data_obs = pd.concat(data_obs)

        label_mappings = {}
        for column_name in column_names:
            label_mappings[column_name] = {}

            id2label = sorted(full_data_obs[column_name].astype(
                'category').cat.categories.tolist())

            label2id = {id2label[i]: i for i in range(len(id2label))}
            label_mappings[column_name]['id2label'] = id2label
            label_mappings[column_name]['label2id'] = label2id

        # Garbage collection
        del data_obs
        del full_data_obs

        write_data(label_mappings, path.join(self.datadir,
                                             'label_mappings.json'))

        self.data_config['label_mappings'] = path.join(self.datadir,
                                                       'label_mappings.json')

    def get_updated_config(self):
        """This function returns updated configs."""
        return self.data_config