#!/usr/bin/env python

from __future__ import print_function   # To allow use of python3-style 'print' in python2

import sys
import argparse
import re
from collections import defaultdict
import numpy as np
import scipy.optimize

######################################################################################

class ParseError(Exception):
    pass

class FeatureMismatchError(Exception):
    pass

class DimensionMismatchError(Exception):
    pass

class ProbabilityZeroError(Exception):
    pass

######################################################################################
### Functions for reading training data and model specification from file

token_re = r'(?:\([^\(\)]*\)|\S)+'

def parse(line):
    try:
        [freq, lhs, rhs, feats] = re.split('\s*\|\s*', line)
    except ValueError:
        raise ParseError("Wrong number of fields")
    try:
        freq = int(freq)
    except ValueError:
        raise ParseError("Not an integer value")
    assert re.match('^'+token_re+'$', lhs), ("Left hand side is not a valid token: %s" % lhs)
    rhs_tokens = tuple(re.findall(token_re, rhs))
    feats_tokens = feats.split()
    try:
        feats_vals = list(map(float, feats_tokens))
    except ValueError:
        raise ParseError("Not a float value")
    return (freq, lhs, rhs_tokens, np.array(feats_vals))

def read_input(filename):
    with open(filename, 'r') as fp:
        (line, linenum) = (fp.readline(), 1)
        while line:
            line = line.strip()
            if line != "" and not re.match(r'^#', line):
                try:
                    data = parse(line)
                    yield data
                except ParseError:
                    print("Parse error on line %d" % linenum, file=sys.stderr)
                    raise
            (line, linenum) = (fp.readline(), linenum+1)

# extract_training_data returns a mapping from LHSs to mappings from RHSs to frequencies
def extract_training_data(inp):
    td = defaultdict(lambda: defaultdict(lambda: 0))
    for (freq, lhs, rhs) in inp:
        td[lhs][rhs] += freq
    return td

######################################################################################
### Class for log-linear models

# This is intended as an abstract base class
class LogLinModel():

    def __init__(self):
        print("WARNING: Constructor for LogLinModel -- this is intended as abstract base class, are you sure this is what you want?", file=sys.stderr)

    def score(self, weights, x, y):
        return self.featvec_dot(x, y, weights)

    # Provided as a hook for specialized implementations to override
    def featvec_dot(self, x, y, othervec):
        return np.dot(othervec, self.featvec(x,y))

    def probs_from_model(self, weights):
        probtable = {}
        for x in self.lhss():
            probtable[x] = {}
            ys = self.rhss(x)
            scores = np.array([self.score(weights,x,y) for y in ys])
            # Using the exp-normalize trick from here: https://timvieira.github.io/blog/post/2014/02/11/exp-normalize-trick/
            b = scores.max()
            shifted_exp_scores = np.exp(scores - b)
            normalized_probs = shifted_exp_scores / shifted_exp_scores.sum()
            for (y, p) in zip(ys, normalized_probs):
                probtable[x][y] = p
        return probtable

    def train(self, td, regularization_lambda, initial=None, method=None):
        if initial is None:
            initial = np.zeros(self.dim())
        if method is None:
            method = 'BFGS'
        objective = lambda weights: penalty(regularization_lambda, weights) - self.loglikelihood(td, weights)
        gradient = lambda weights: penaltygrad(regularization_lambda, weights) - self.loglhdgrad(td, weights)
        res = scipy.optimize.minimize(objective, initial, jac=gradient, method=method)
        print("Optimization results:")
        print("         Function value:       %f" % res.fun)
        print("         Iterations:           %d" % res.nit)
        print("         Function evaluations: %d" % res.nfev)
        print("         Gradient evaluations: %d" % res.njev)
        print("         %s" % res.message)
        return res.x

    def loglikelihood(self, td, weights):
        probtable = self.probs_from_model(weights)
        def logprob(x,y):
            prob = probtable[x][y]
            if prob == 0.0:
                raise ProbabilityZeroError
            else:
                return np.log(prob)
        try:
            ll = 0
            for (lhs,d) in td.items():
                ll += sum([ freq * logprob(lhs,rhs) for (rhs,freq) in d.items() ])
        except ProbabilityZeroError:
            ll = np.finfo('float').min  # the smallest number possible
        return np.longdouble(ll)

    # This function corresponds to equation (6) in Mike Collins' notes here:
    #       http://www.cs.columbia.edu/~mcollins/loglinear.pdf
    # First note that the right-hand side of equation(6):
    #       sum_i [ f_k(x_i, y_i) ]  -  sum_i sum_y [ p(y, x_i, v) f_k(x_i, y) ]
    # can be re-expressed as
    #       sum_i [ f_k(x_i, y_i)  -  sum_y [ p(y, x_i, v) f_k(x_i, y) ] ]
    # This determines a number for each value of k; or, a vector with one entry 
    # for each value of k. That vector is what this function computes. 
    # The subtraction can be thought of as subtracting the expected contribution 
    # of the kth feature from the observed contribution.
    def loglhdgrad(self, td, weights):

        # Precompute p(y,x,v) for each (x,y) pair
        probtable = self.probs_from_model(weights)

        # Precompute the vector 'sum_y [ p(y, x_i, v) f_k(x_i, y) ]' for each x
        expectation = {}
        for lhs in self.lhss():
            foo = np.zeros(self.dim())
            for rhsp in self.rhss(lhs):
                featvec = self.featvec(lhs,rhsp)
                foo += probtable[lhs][rhsp] * featvec
            expectation[lhs] = foo

        # Now compute the overall result by cycling through the training data.
        # Our 'freq' does not show up in Collins' equations: it's the number of 
        # times this particular (lhs,rhs) pair shows up in Collins' i-indexed training set.
        result = np.zeros(self.dim())
        for (lhs,d) in td.items():
            for (rhs,freq) in d.items():
                result += freq * (self.featvec(lhs,rhs) - expectation[lhs])

        assert isinstance(result, np.ndarray) and result.ndim == 1
        return np.longdouble(result)

    def report_model(self, weights):
        probtable = self.probs_from_model(weights)
        print("######################################")
        for lhs in self.lhss():
            for rhs in sorted(self.rhss(lhs)):
                print("%12.6f\t%.6f\t%s --> %s" % (self.score(weights,lhs,rhs), probtable[lhs][rhs], lhs, " ".join(rhs)))
        print("######################################")

# Subclass for models specified via a file with rules' feature-vectors and their training frequencies together
class LogLinModelFromFile(LogLinModel):

    # Sets up these instance variables:
    #   self._rules: a mapping from LHSs to mappings from RHSs to feature vectors
    #   self._dim: the number of parameters
    def __init__(self, inp):
        self._rules = defaultdict(lambda: {})
        self._dim = None
        for (lhs, rhs, feats) in inp:
            if self._dim is None:
                self._dim = len(feats)
            else:
                assert self._dim == len(feats), "Mismatching dimensions"
            old_feats = self._rules[lhs].get(rhs, None)
            if old_feats is not None:
                assert np.array_equal(old_feats, feats), ("Mismatching features for lhs %s and rhs %s" % (lhs,rhs))
            else:
                self._rules[lhs][rhs] = np.array(feats)

    def dim(self):
        return self._dim

    def lhss(self):
        return self._rules.keys()

    def rhss(self, lhs):
        return self._rules[lhs].keys()

    def featvec(self, lhs, rhs):
        return self._rules[lhs][rhs]

# Subclass for models with only ``basic'' features, in the sense of Berg-Kirkpatrick et al 2010 p.583, 
# i.e. indicator features that emulate classical generative models
class LogLinModelBasic(LogLinModel):

    # Sets up these instance variables:
    #   self._rulelist: a list of (LHS,RHS) pairs
    def __init__(self, rulelist):
        self._rulelist = list(rulelist)
        self._featvecdict = {}
        self._indexdict = {}

    def dim(self):
        return len(self._rulelist)

    def lhss(self):
        return list(set([lhs for (lhs,rhs) in self._rulelist]))

    def rhss(self, x):
        return list(set([rhs for (lhs,rhs) in self._rulelist if x == lhs]))

    def find_index(self, lhs, rhs):
        try:
            return self._indexdict[(lhs,rhs)]
        except KeyError:
            index = self._rulelist.index((lhs,rhs))
            self._indexdict[(lhs,rhs)] = index
            return index

    def featvec(self, lhs, rhs):
        try:
            return self._featvecdict[(lhs,rhs)]
        except KeyError:
            index = self.find_index(lhs,rhs)
            v = np.zeros(self.dim())
            v.put(index,1)
            self._featvecdict[(lhs,rhs)] = v
            return v

    def featvec_dot(self, x, y, othervec):
        index = self.find_index(x,y)
        return othervec[index]

######################################################################################
### Regularization/priors; providing L2 regularization as the only option for now

def penalty(lam, weights):
    return (lam / 2) * (np.linalg.norm(weights)**2)

def penaltygrad(lam, weights):
    return lam * weights

######################################################################################

def run(filename, regularization_lambda):

    # This input data contains both rules' feature vectors and their training frequencies together
    input_data = list(read_input(filename))
    #m = LogLinModelBasic([(lhs,rhs) for (freq,lhs,rhs,feats) in input_data])
    m = LogLinModelFromFile([(lhs,rhs,feats) for (freq,lhs,rhs,feats) in input_data])
    td = extract_training_data([(freq,lhs,rhs) for (freq,lhs,rhs,feats) in input_data])

    # Do the optimization
    weights = m.train(td, regularization_lambda)
    print("Found optimal parameter values:", weights)
    llhd = m.loglikelihood(td, weights)
    penalty_term = penalty(regularization_lambda, weights)
    print("At this point:  penalty - log-likelihood  =  %f - %f  =  %f" % (penalty_term, llhd, penalty_term - llhd))

    # Print out the rules with their optimized probabilities
    m.report_model(weights)

def main(argv):
    argparser = argparse.ArgumentParser()
    argparser.add_argument("training_file", metavar="TRAINING_FILE", type=str, help="File containing training data")
    argparser.add_argument("-l2", dest="l2_lambda", metavar="LAMBDA", type=float, default=0, help="Lambda parameter value for L2 regularization")
    args = argparser.parse_args()
    print("Training data file:", args.training_file, file=sys.stderr)
    print("Regularization parameter lambda:", args.l2_lambda, file=sys.stderr)
    run(args.training_file, args.l2_lambda)
    print("Done, exiting", file=sys.stderr)

if __name__ == "__main__":
    main(sys.argv)

