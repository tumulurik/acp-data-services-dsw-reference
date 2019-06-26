#####################################################################
# ADOBE CONFIDENTIAL
# ___________________
#
#  Copyright 2018 Adobe
#  All Rights Reserved.
#
# NOTICE:  All information contained herein is, and remains
# the property of Adobe and its suppliers, if any. The intellectual
# and technical concepts contained herein are proprietary to Adobe
# and its suppliers and are protected by all applicable intellectual
# property laws, including trade secret and copyright laws.
# Dissemination of this information or reproduction of this material
# is strictly forbidden unless prior written permission is obtained
# from Adobe.
#####################################################################

from ml.runtime.python.Interfaces.AbstractScorer import AbstractScorer
from data_access_sdk_python.reader import DataSetReader
from data_access_sdk_python.writer import DataSetWriter
import tensorflow as tf

import pandas as pd
fc = tf.feature_column
tf.set_random_seed(123)

import os
import json

class Scorer(AbstractScorer):
    def score(self, config={}):
        tf.logging.set_verbosity(tf.logging.ERROR)
        tf_config = json.loads(os.environ['TF_CONFIG'])
        tf_config = json.loads('{}')
        os.environ['TF_CONFIG'] = json.dumps(tf_config)


        #########################################
        # Load Data
        #########################################
        prodreader = DataSetReader(client_id=config['ML_FRAMEWORK_IMS_USER_CLIENT_ID'],
                                   user_token=config['ML_FRAMEWORK_IMS_TOKEN'],
                                   service_token=config['ML_FRAMEWORK_IMS_ML_TOKEN'])

        dataframe = prodreader.load(data_set_id=config['scoringDataSetId'],
                                    ims_org=config['ML_FRAMEWORK_IMS_ORG_ID'])



        #########################################
        # Data Preparation/Feature Engineering
        #########################################
        if '_id' in dataframe.columns:
            # Rename columns to strip tenantId
            dataframe = dataframe.rename(columns=lambda x: str(x)[str(x).find('.') + 1:])
            # Drop id, eventType and timestamp
            dataframe.drop(['_id', 'eventType', 'timestamp'], axis=1, inplace=True)

        dataframe.date = pd.to_datetime(dataframe.date)
        dataframe['week'] = dataframe.date.dt.week
        dataframe['year'] = dataframe.date.dt.year

        dataframe = dataframe.sort_values(by=['date', 'store'])

        dataframe = pd.concat([dataframe, pd.get_dummies(dataframe['storeType'])], axis=1)
        dataframe.drop('storeType', axis=1, inplace=True)
        dataframe['isHoliday'] = dataframe['isHoliday'].astype(int)

        dataframe['weeklySalesAhead'] = dataframe.shift(-45)['weeklySales']
        dataframe['weeklySalesLag'] = dataframe.shift(45)['weeklySales']
        dataframe['weeklySalesDiff'] = (dataframe['weeklySales'] - dataframe['weeklySalesLag']) / dataframe[
            'weeklySalesLag']
        dataframe.dropna(0, inplace=True)

        dataframe = dataframe.set_index(dataframe.date)
        dataframe.drop('date', axis=1, inplace=True)



        #########################################
        # Data Preparation/Feature Engineering
        #########################################
        X_test = dataframe.drop('weeklySalesAhead', axis=1)
        y_test = dataframe['weeklySalesAhead'].values

        features = []
        for feature in X_test.columns:
            features.append(fc.numeric_column(feature, dtype=tf.float32))

        def gen_input_fn(features, labels, epochs=10, shuffle=True, batch_size=32):
            def input_function():
                ds = tf.data.Dataset.from_tensor_slices((dict(features), labels))
                if shuffle:
                    ds = ds.shuffle(1000)
                ds = ds.batch(batch_size).repeat(epochs)
                return ds
            return input_function

        test_input_fn = gen_input_fn(X_test, y_test, shuffle=False, epochs=1)



        #########################################
        # BoostedTreesRegressor Model
        #########################################
        model = tf.estimator.BoostedTreesRegressor(features,
                                                   n_batches_per_layer=5,
                                                   model_dir=config['modelPATH'])



        #########################################
        # Write Results
        #########################################
        pred_dict = list(model.predict(test_input_fn))
        y_pred = pd.Series([pred['predictions'][0] for pred in pred_dict])

        X_test['prediction'] = y_pred.values
        output = X_test[['store', 'prediction']].reset_index()
        output['date'] = output['date'].astype(str)

        writer = DataSetWriter(client_id=config['ML_FRAMEWORK_IMS_USER_CLIENT_ID'],
                               user_token=config['ML_FRAMEWORK_IMS_TOKEN'],
                               service_token=config['ML_FRAMEWORK_IMS_ML_TOKEN'])

        print('Writer Configured')

        tenant_id = config['tenant_id']
        output = output.add_prefix(tenant_id + '.')
        output = output.join(pd.DataFrame(
            {
                '_id': '',
                'timestamp': '2019-01-01T00:00:00',
                'eventType': ''
            }, index=output.index))

        writer.write(data_set_id=config['output_dataset_id'],
                     dataframe=output,
                     ims_org=config['ML_FRAMEWORK_IMS_ORG_ID'],
                     file_format='json')

        print('Write Done')