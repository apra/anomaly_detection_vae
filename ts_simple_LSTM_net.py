import torch
import torch.nn as nn
import torch.distributions as distributions


#Standard_LSTM
# the Baseline LSTM approach.
class Standard_LSTM(nn.Module):
    def __init__(self, input_dimension, param_size, hidden_dim):
        super(Standard_LSTM, self).__init__()

        # parameters
        self.input_dimension = input_dimension
        self.hidden_dim = hidden_dim
        self.param_size = param_size

        # activation and dropout
        self.relu = nn.ReLU()
        self.dropout = nn.Dropout(0.1)

        # model
        self.lstm = nn.LSTM(input_dimension, hidden_dim, num_layers=2)
        self.hidden2hidden = nn.Linear(hidden_dim, hidden_dim)
        self.hidden2params = nn.Linear(hidden_dim, param_size * input_dimension)

    def forward(self, x, device):
        outputs = {}
        outputs["x_input"] = x
        x = x.permute(1, 0, 2)
        # lstm_out is the output of the last layer of hidden units [seq_len, batch, num_directions * hidden_size]
        # h is the hidden states at the last time step
        # c is the cell state at the last time step
        lstm_out, (h, c) = self.lstm(x)
        # linear wants [batch, seq_len, hidden_size]
        # linear_in = self.dropout(self.relu(self.hidden2hidden(lstm_out)))
        linear_in = self.dropout(self.relu(lstm_out))

        # take output of hidden layers at each time step h_t and run it through a fully connected layer
        params = self.hidden2params(linear_in)

        outputs["params"] = params
        outputs["param_size"] = self.param_size
        return outputs


# loss function used for Gaussian normal distribution of the signals
# arguments:
#   - model_output: the output of the model
#   - device: where to place the data
def loss_function_normal(model_output, device):
    # unpack the required quantities
    x_true = model_output["x_input"].permute(1, 0, 2)

    input_dimension = x_true.shape[2]

    # check to see if something went wrong with selecting the right loss function and network pair
    if model_output["params"].shape[2] != 2 * input_dimension:
        raise ValueError("Wrong input dimensions or number of parameters in the output")

    # extrapolate parameters
    mu, log_var = torch.chunk(model_output["params"], 2, dim=2)
    sigma = torch.exp(log_var / 2)
    #get the length of the sequence
    seq_length = mu.shape[0]
    # iterate over each time step in the sequence to compute NLL
    t = 0
    # define the distribution
    p = distributions.Normal(mu[t, :, :], sigma[t, :, :])
    log_prob = torch.mean(p.log_prob(x_true[t + 1, :, :]), dim=-1)

    for t in range(1, seq_length - 1):
        # define the distribution
        p = distributions.Normal(mu[t, :, :], sigma[t, :, :])

        log_prob += torch.mean(p.log_prob(x_true[t + 1, :, :]), dim=-1)
        # print(x_true.shape)

    NLL = - torch.mean(log_prob, dim=0) / seq_length
    return {
        "loss": NLL,
        "NLL": NLL
    }
