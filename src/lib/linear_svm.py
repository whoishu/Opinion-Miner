#!/usr/bin/env python
#
# Copyright 2011 Trung Huynh
#
# Licnesed under GNU GPL, Version 3.0; you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.gnu.org/licenses/gpl.html
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import csv
import os
import logging
import logging.handlers
from itertools import izip
import re
import MySQLdb
import socket
import SocketServer
import demjson
from threading import Lock
from argparse import ArgumentParser
from ConfigParser import ConfigParser
from data_processing.vectorize import Vectorizer


LEVELS = {
    'debug': logging.DEBUG,
    'info': logging.INFO,
    'warning': logging.WARNING,
    'error': logging.ERROR,
    'critical': logging.CRITICAL,
    'quiet': logging.NOTSET
}
          
class LinearSVM(object):

    @classmethod
    def init(cls, config="server.cfg"):
        """Initialize LinearSVM
        
        """
        config_parser = ConfigParser()
        try:
            config_parser.readfp(open(config))
        except Exception, e:
            raise Exception("Can't parse config file %s:: %s" % (config, e))
            
        try:
            cls.path = config_parser.get("database", "path")
            cls.model_file = os.path.join(cls.path, config_parser.get("database", "model"))
            cls.index_file = os.path.join(cls.path, config_parser.get("database", "index"))
            cls.log_mode = LEVELS.get(config_parser.get("runtime", "mode"), logging.NOTSET)
            cls.log_size = config_parser.get("runtime", "log_size")
            cls.log = config_parser.get("runtime", "log")
            cls.ngram = int(config_parser.get("data", "ngram"))
            cls.class_weights = bool(int(config_parser.get("output", "class_weights")))
            cls.feature_weights = bool(int(config_parser.get("output", "feature_weights")))
        except Exception, e:
            raise Exception("Invalid config file:: %s" % e)

        feature_regex = re.compile("\w{2,}")

        cls.logger = logging.getLogger(__name__)
        cls.logger.setLevel(cls.log_mode)
        loggingHandler = logging.handlers.RotatingFileHandler(
            cls.log,
            maxBytes=int(cls.log_size),
            backupCount=5
        )
        loggingHandler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        cls.logger.addHandler(loggingHandler)          
        
        
        cls.fm = csv.reader(open(cls.model_file, "r"), delimiter=" ")
        cls.solver_type = cls.fm.next()[-1]
        cls.nr_class = int(cls.fm.next()[-1])
        cls.nr_weight = 1 if cls.nr_class == 2 else cls.nr_class
        #TODO
        #right condition is:
        #if(nr_class==2 && model_->param.solver_type != MCSVM_CS)
        
        cls.labels = cls.fm.next()[1:]
        cls.dblabels = map(lambda l: "class_"+l if l != "-1" else "negative", cls.labels)
        cls.nr_feature = int(cls.fm.next()[-1])
        cls.bias = cls.fm.next()[-1]
        cls.lock = Lock()
    
    
    @classmethod
    def save_to_db(cls):
        # skip "w" line
        cls.fm.next()     
        
        fi = csv.reader(open(cls.index_file, "r"), delimiter="\t")
        
        sql_batch = 20
        sql = "INSERT IGNORE INTO svm_features "\
              "(id, name, " + ",".join(cls.dblabels[:cls.nr_weight]) + ")"\
              "VALUES (%s, %s, "+",".join(["%s"]*cls.nr_weight)+")"

        values = []
        for weights, index_name_pair in izip(cls.fm, fi):
            index, name = index_name_pair
            value = [index, name]
            value.extend(weights[:cls.nr_weight])
            values.append(tuple(value))
            if len(values) >= sql_batch:
                cls.cursor.executemany(sql, values)
                values = []
                
        if len(values) > 0: cls.cursor.executemany(sql, values)
            
    
    @classmethod
    def db_connect(cls, host, user, passwd, db):
        cls.db = MySQLdb.connect(host, user, passwd, db)
        cls.cursor = cls.db.cursor()
        
    @classmethod
    def vectorize(cls, review):
        names = Vectorizer.get_ngrams(review, cls.ngram)
        sql = "SELECT name," + ",".join(cls.dblabels[:cls.nr_weight]) + "\
               FROM svm_features\
               WHERE name=%s"
        vector = {}
        cursor = cls.db.cursor()
        for name in names:
            cursor.execute(sql, name)
            f = cursor.fetchone()
            if f is not None: vector[f[0]] = f[1:]
            
        return vector
        
    @classmethod
    def predict_vector(cls, vector, return_class_weights=False):
        dec_values = [0] * cls.nr_weight
        for k, w in vector.items():
            for i in xrange(len(w)):
                dec_values[i] += w[i]
                
        if cls.nr_class == 2:
            lbl = cls.dblabels[0] if dec_values[0] > 0 else cls.dblabels[1]
        else:
            lbl = cls.dblabels[dec_values.index(max(dec_values))]
        
        class_weights = None
        if return_class_weights:
            class_weights = dict(zip(cls.labels, dec_values))
            
            
        return lbl, class_weights
        
    @classmethod
    def predict(cls, review):
        score, class_weights = cls.predict_vector(cls.vectorize(review), cls.class_weights)
        return dict(
            score = score.split("_")[-1],
            weights = class_weights            
        )
    
if __name__ == "__main__":
    parser = ArgumentParser(description="Linear SVM Model")
    parser.add_argument("--config", dest="config", default="server.cfg")
    parser.add_argument("--host", dest="host", default=None)
    parser.add_argument("--user", dest="user", default=None)
    parser.add_argument("--passwd", dest="passwd", default=None)
    parser.add_argument("--db", dest="db", default=None)                        
    LinearSVM.args = parser.parse_args()
    LinearSVM.init(LinearSVM.args.config)        
    LinearSVM.db_connect(
        LinearSVM.args.host,
        LinearSVM.args.user,
        LinearSVM.args.passwd,
        LinearSVM.args.db
    )
    LinearSVM.save_to_db()
