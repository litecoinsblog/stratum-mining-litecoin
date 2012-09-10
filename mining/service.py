import binascii
from twisted.internet import defer

from stratum.services import GenericService, admin
from stratum.custom_exceptions import ServiceException
from stratum.pubsub import Pubsub
from interfaces import Interfaces
from subscription import MiningSubscription

import stratum.logger
log = stratum.logger.get_logger('mining')

class SubmitException(ServiceException):
    pass
                
class MiningService(GenericService):
    '''This service provides public API for Stratum mining proxy
    or any Stratum-compatible miner software.
    
    Warning - any callable argument of this class will be propagated
    over Stratum protocol for public audience!'''
    
    service_type = 'mining'
    service_vendor = 'stratum'
    is_default = True
    
    @admin
    def update_block(self):
        '''Connect this RPC call to 'bitcoind -blocknotify' for 
        instant notification about new block on the network.
        See blocknotify.sh in /scripts/ for more info.'''
        Interfaces.template_registry.update_block()
        return True 
    
    def authorize(self, worker_name, worker_password):
        '''Let authorize worker on this connection.'''
        
        session = self.connection_ref().get_session()
        session.setdefault('authorized', {})
        
        if Interfaces.worker_manager.authorize(worker_name, worker_password):
            session['authorized'][worker_name] = worker_password
            return True
        
        else:
            if worker_name in session['authorized']:
                del session['authorized'][worker_name]
            return False
        
    def subscribe(self):
        '''Subscribe for receiving mining jobs. This will
        return subscription details, extranonce1_hex and extranonce2_size'''
        
        extranonce1 = Interfaces.template_registry.get_new_extranonce1()
        extranonce2_size = Interfaces.template_registry.extranonce2_size

        session = self.connection_ref().get_session()
        session['extranonce1'] = extranonce1
        session['difficulty'] = 1 # Following protocol specs, default diff is 1

        extranonce1_hex = binascii.hexlify(extranonce1)
            
        return Pubsub.subscribe(self.connection_ref(), MiningSubscription()) + (extranonce1_hex, extranonce2_size)
    
    '''    
    def submit(self, worker_name, job_id, extranonce2, ntime, nonce):
        import time
        start = time.time()
        
        for x in range(100):
            try:
                ret = self.submit2(worker_name, job_id, extranonce2, ntime, nonce)
            except:
                pass
            
        log.info("LEN %.03f" % (time.time() - start))
        return ret
    '''
    
    def submit(self, worker_name, job_id, extranonce2, ntime, nonce):
        '''Try to solve block candidate using given parameters.'''
        
        session = self.connection_ref().get_session()
        session.setdefault('authorized', {})
        
        # Check if worker is authorized to submit shares
        if not Interfaces.worker_manager.authorize(worker_name,
                        session['authorized'].get(worker_name)):
            raise SubmitException("Worker is not authorized")

        # Check if extranonce1 is in connection session
        extranonce1_bin = session.get('extranonce1', None)
        if not extranonce1_bin:
            raise SubmitException("Connection is not subscribed for mining")
        
        difficulty = session['difficulty']

        # This checks if submitted share meet all requirements
        # and it is valid proof of work.
        (is_valid, reason, block_header, block_hash) = Interfaces.template_registry.submit_share(job_id,
                                                worker_name, extranonce1_bin, extranonce2, ntime, nonce, difficulty,
                                                Interfaces.share_manager.on_submit_block)
   
        # block_header and block_hash may be None when submitted data are corrupted     
        Interfaces.share_manager.on_submit_share(worker_name, block_header, block_hash, difficulty,
                                                 Interfaces.timestamper.time(), is_valid)
        
        if not is_valid:
            raise SubmitException(reason)

        return True
            
    # Service documentation for remote discovery
    update_block.help_text = "Notify Stratum server about new block on the network."
    update_block.params = [('password', 'string', 'Administrator password'),]
    
    authorize.help_text = "Authorize worker for submitting shares on this connection."
    authorize.params = [('worker_name', 'string', 'Name of the worker, usually in the form of user_login.worker_id.'),
                        ('worker_password', 'string', 'Worker password'),]
    
    subscribe.help_text = "Subscribes current connection for receiving new mining jobs."
    subscribe.params = []
    
    submit.help_text = "Submit solved share back to the server. Excessive sending of invalid shares "\
                       "or shares above indicated target (see Stratum mining docs for set_target()) may lead "\
                       "to temporary or permanent ban of user,worker or IP address."
    submit.params = [('worker_name', 'string', 'Name of the worker, usually in the form of user_login.worker_id.'),
                     ('job_id', 'string', 'ID of job (received by mining.notify) which the current solution is based on.'),
                     ('extranonce2', 'string', 'hex-encoded big-endian extranonce2, length depends on extranonce2_size from mining.notify.'),
                     ('ntime', 'string', 'UNIX timestamp (32bit integer, big-endian, hex-encoded), must be >= ntime provided by mining,notify and <= current time'),
                     ('nonce', 'string', '32bit integer, hex-encoded, big-endian'),]
        