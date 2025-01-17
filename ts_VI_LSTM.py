"""
Time Series Variational Inference LSTM

@author: Samuele Papa, Henrik Hviid Hansen
"""
import torch
from torch import nn, distributions

#Variational LSTM
# the VI LSTM approach.
class Variational_LSTM(nn.Module):
    def __init__(self, input_dim, param_dist, hidden_dim_rec, hidden_dim_gen, latent_dim):
        super(Variational_LSTM, self).__init__()

        # parameters
        self.latent_dim = latent_dim
        self.input_dim = input_dim
        self.hidden_dim_rec = hidden_dim_rec
        self.hidden_dim_gen = hidden_dim_gen

        # activation and dropouts
        self.relu = nn.ReLU()
        self.dropout1 = nn.Dropout(p=0.1)
        self.dropout2 = nn.Dropout(p=0.1)

        # encoder net, recognition model q(z_t+1|x_1:t)
        self.encoder_LSTM = nn.LSTM(input_size=input_dim,
                                    hidden_size=hidden_dim_rec,  # 2 because we want mu and sigma
                                    num_layers=1
                                    )
        self.encoder_hidden2hidden = nn.Linear(hidden_dim_rec, hidden_dim_rec)

        self.enc2latent = nn.Linear(hidden_dim_rec, 2 * latent_dim)

        # decoder net p(x_t+1|x_1:t,z_1:t)
        self.decoder_LSTM = nn.LSTM(input_size=input_dim + latent_dim,
                                    hidden_size=hidden_dim_gen,
                                    num_layers=1
                                    )

        self.decoder_hidden2hidden = nn.Linear(hidden_dim_gen, hidden_dim_gen)

        self.dec2features = nn.Linear(hidden_dim_gen, param_dist * input_dim)

    def forward(self, x, device):
        outputs = {}
        outputs["x_input"] = x
        batch_size = x.shape[0]
        seq_length = x.shape[1]
        # --- recognition LSTM to generate latent variable(s) for each time step --- #

        # get x in the right format: [seq_length, batch_size, input_dimension]
        x_encoder = x.permute(1, 0, 2)
        encoder_out, (h, c) = self.encoder_LSTM(x_encoder)
        # add non-linearities and a level of abstraction
        # encoder_out = self.relu(self.encoder_hidden2hidden(encoder_out))
        # encoder_out = self.relu(encoder_out)
        # [seq_len, batch_dim, features]

        # outputs the latent variable parameters mu and sigma
        x_latent = self.enc2latent(self.dropout1(self.relu(encoder_out)))

        # make the network learn the log of the variance it is non-negative (log only takes pos x)
        mu, log_var = torch.chunk(x_latent, 2, dim=-1)
        sigma = torch.exp(log_var / 2)  # std of the latent variable distribution
        # reparameterization trick happens here:
        epsilon = 0
        with torch.no_grad():
            epsilon = torch.randn(seq_length,
                                  batch_size,
                                  self.latent_dim,
                                  ).to(device)
        # create the random latent variable, reparametrization trick
        z = mu + sigma * epsilon

        # store intermediate results and latent variables
        outputs["z"] = z
        outputs["z_mu"] = mu
        outputs["z_sigma"] = sigma
        outputs["z_log_var"] = log_var

        # --- ordinary LSTM to predict the next x in the time sequence --- #
        # concatenate the latent variables with the original input x
        # [sequence_len, batch_size, dimensions]
        x_aug = torch.cat((x_encoder, z), dim=-1)  # aug = augmented x, because we augment x with z

        # run it through the ordinary LSTM
        decoder_out, (h, c) = self.decoder_LSTM(x_aug)
        # decoder_out = self.relu(self.decoder_hidden2hidden(decoder_out))
        # decoder_out = self.relu(decoder_out)

        # symmetry with encoder
        # [sequence_len, batch_size, dimensions]
        params = self.dec2features(self.dropout2((self.relu(decoder_out))))

        # store the outputs in the form (batch_size, seq_length, input_dim)
        outputs["params"] = params

        return outputs

# loss function used for Gaussian normal distribution of the signals
# arguments:
#   - model_output: the output of the model
#   - device: where to place the data
def loss_normal2d(model_output, device, beta):
    # unpack the required quantities
    x_true = model_output["x_input"].permute(1, 0, 2)
    params = model_output["params"]
    mu, logvar = torch.chunk(params, 2, dim=-1)
    sigma = torch.exp(logvar / 2)

    z_mu = model_output["z_mu"]
    z_log_var = model_output["z_log_var"]

    seq_length = mu.shape[0]

    t = 0
    # define the distribution
    p = distributions.Normal(mu[t, :, :], sigma[t, :, :])
    log_prob = torch.sum(p.log_prob(x_true[t + 1, :, :]), dim=-1)
    # dimensions [batch_size, dimension]
    ones_vector = torch.ones((z_mu.shape[1], z_mu.shape[2])).to(device)
    # KL-divergence
    negative_kl = 0.5 * torch.sum(ones_vector + z_log_var[t, :, :] - z_mu[t, :, :] ** 2 - torch.exp(z_log_var[t, :, :]),
                                  dim=-1)
    # KL divergence through time
    kl_tt = -negative_kl

    # iterate over each time step in the sequence to compute NLL and KL terms
    for t in range(1, seq_length - 1):
        # define the distribution
        p = distributions.Normal(mu[t, :, :], sigma[t, :, :])

        log_prob += torch.sum(p.log_prob(x_true[t + 1, :, :]), dim=-1)
        # KL-divergence
        negative_kl += 0.5 * torch.sum(ones_vector + z_log_var[t, :, :] - z_mu[t, :, :] ** 2 -
                                       torch.exp(z_log_var[t, :, :]), dim=-1)

        kl_tt -= negative_kl
    # average over the batches and divide by the sequence len-1 to get size independent from the length of the sequence
    NLL, KL = -torch.mean(log_prob, dim=0) / (seq_length - 1), torch.mean(kl_tt, dim=0) / (seq_length - 1)

    ELBO = NLL + beta * KL

    return {
        "loss": ELBO,
        "ELBO": ELBO,
        "NLL": NLL,
        "KL": KL
    }

# loss function used for Exponential normal distribution of the signals. Is not used in the testing.
# arguments:
#   - model_output: the output of the model
#   - device: where to place the data
def loss_normal2d_exponential(model_output, device, beta):
    # unpack the required quantities
    x_true = model_output["x_input"].permute(1, 0, 2)
    params = model_output["params"]
    rate = torch.exp(params)
    # sigma = torch.exp(logvar / 2)

    z_mu = model_output["z_mu"]
    z_log_var = model_output["z_log_var"]

    seq_length = rate.shape[0]
    # iterate over each time step in the sequence to compute NLL and KL terms

    t = 0
    # define the distribution
    p = distributions.Exponential(rate[t, :, :])
    log_prob = torch.sum(p.log_prob(x_true[t + 1, :, :]), dim=-1)
    # dimensions [batch_size, dimension]
    ones_vector = torch.ones((z_mu.shape[0], z_mu.shape[2])).to(device)
    # KL-divergence
    negative_kl = 0.5 * torch.sum(ones_vector + z_log_var[t, :, :] - z_mu[t, :, :] ** 2 - torch.exp(z_log_var[t, :, :]),
                                  dim=-1)

    # KL divergence through time
    kl_tt = -negative_kl

    for t in range(1, seq_length - 1):
        # define the distribution
        #        p = distributions.Normal(mu[:, t, :], sigma[:, t, :])
        #        p = distributions.LogNormal(mu[t, :, :], sigma[t, :, :])
        p = distributions.Exponential(rate[t, :, :])

        log_prob += torch.sum(p.log_prob(x_true[t + 1, :, :]), dim=-1)
        # KL-divergence
        negative_kl += 0.5 * torch.sum(ones_vector + z_log_var[t, :, :] - z_mu[t, :, :] ** 2 -
                                       torch.exp(z_log_var[t, :, :]))

        kl_tt -= negative_kl

    NLL, KL = -torch.mean(log_prob, dim=-1) / (seq_length - 1), torch.mean(kl_tt, dim=0) / (
            seq_length - 1)

    ELBO = NLL + beta * KL

    return {
        "loss": ELBO,
        "ELBO": ELBO,
        "NLL": NLL,
        "KL": KL
    }
