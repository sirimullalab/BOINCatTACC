#!/usr/bin/env python3


"""
BASICS

Creates an automated dockerfile for BOINC jobs. If the build fails, submits an email to the user with the error logs.
All images are named {TOKEN}:{10 letters sha256 hash}.
If the build succeeds, then the memory it occupies is reduced from the user's account
"""



import os, sys, shutil
import custodian as cus
import docker
import email_common as ec
import json
import redis
import hashlib
import datetime
from midas_processing import midas_reader as mdr
import mirror_interactions as mirror
import mysql_interactions as mints
import preprocessing as pp
import requests
import uuid



r = redis.Redis(host = '0.0.0.0', port = 6389, db = 2)
# Starts a client for docker
client = docker.from_env()
image = client.images
container =  docker.from_env().containers


Success_Message = "Your MIDAS job has generated an image submitted for processing.\nThis message was completed on DATETIME UTC."
Failure_Message = "Your MIDAS job has failed dockerfile construction.\nThis message was sent on DATETIME UTC."

# Creates a new docker image
# Designed so that it is easy to silence for further testing

# IMTAG (str): tag for the image
# UTOK (str): The user's token
# MIDIR (str): Midas directory
# FILES_PATH (str): Path to the files, most likely it will be the current directory
# COMMAND_TXT (str): Text file with the BOINC command
# DOCK_DOCK (str): Actual dockerfile text
# BOCOM (str): Boinc command to run
def user_image(IMTAG, FILES_PATH = '.'):

    image.build(path=FILES_PATH, tag=IMTAG.lower(), timeout=180)


# Full process of building a docker image
def complete_build(IMTAG, UTOK, MIDIR, COMMAND_TXT, DOCK_DOCK, BOCOM, FILES_PATH='.'):

    researcher_email = pp.obtain_email(UTOK)
    try:
        user_image(IMTAG)

        # Reduces the corresponding user's allocation
        # Docker represents image size in GB
        # Moves the file
        boapp = r.get(UTOK+';'+MIDIR).decode("UTF-8")
        
        if boapp == "boinc2docker":
            shutil.move(COMMAND_TXT+".txt", "/home/boincadm/project/html/user/token_data/process_files/"+COMMAND_TXT+".txt")


        # VolCon instructions
        # Deletes the image, submits the saved version to a mirror

        if boapp == "volcon":
            
            # Deletes the key
            r.delete(UTOK+';'+MIDIR)

            # Saves the image into a file
            img = image.get(IMTAG)
            resp = img.save()
            random_generated_dir = hashlib.sha256(str(datetime.datetime.now()).encode('UTF-8')).hexdigest()[:4:]
            image_dir = os.getcwd()+"/"+random_generated_dir
            os.mkdir(image_dir)
            full_image_path = image_dir+"/image.tar.gz"
            with open(full_image_path, "wb") as ff:
                for salmon in resp:
                    ff.write(salmon)

            VolCon_ID = uuid.uuid4().hex
            mirror_IP = mirror.get_random_mirror()

            # Move image to the mirror
            mirror.upload_file_to_mirror(full_image_path, mirror_IP, VolCon_ID)

            # Moves the file to where it belongs
            saved_name = "image_"+hashlib.sha256(str(datetime.datetime.utcnow()).encode('UTF-8')).hexdigest()[:4:]+".tar.gz"
            shutil.move(full_image_path, saved_name)

            # Move commands to mirror
            Commands = " ".join(BOCOM.split(" ")[1::])

            # Add job to VolCon
            # Set as medium priority
            mints.add_job(UTOK, "Custom", Commands, 0, VolCon_ID, "Middle", public=0)
            mints.update_mirror_ip(VolCon_ID, mirror_IP)

            # MIDAS cannot accept GPU jobs
            job_info = {"Image":"Custom", "Command":Commands, "TACC":0, "GPU":0,
                        "VolCon_ID":VolCon_ID, "public":0, "key":mirror.mirror_key(mirror_IP)}
            
            requests.post('http://'+mirror_IP+":7000/volcon/mirror/v2/api/public/receive_job_files",
                json=job_info)

            # Moves data to Reef
            requests.post('http://'+os.environ['Reef_IP']+':2001/reef/result_upload/'+os.environ['Reef_Key']+'/'+UTOK,
                    files={"file": open(saved_name, "rb")})

            # Deletes local copy
            os.remove(saved_name)
            # Removes the image
            container.prune()
            image.remove(IMTAG, force=True)

            # Email user with dockerfile
            MESSAGE = Success_Message.replace("DATETIME", datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
            MESSAGE += "\n\nClick on the following link to obtain a compressed version of the application docker image.\n"
            MESSAGE += "You are welcome to upload the image on dockerhub in order to reduce the future job processing time for the same application (no allocation will be discounted): \n"
            MESSAGE += os.environ["SERVER_IP"]+":5060/boincserver/v2/reef/results/"+UTOK+"/"+saved_name.replace("../", "")
            MESSAGE += "\n\nDownload the image using the following command:\n"
            MESSAGE += "curl -O "+os.environ["SERVER_IP"]+":5060/boincserver/v2/reef/results/"+UTOK+"/"+saved_name.replace("../", "")
            MESSAGE += "\nThen load the image (sudo permission may be required):"
            MESSAGE += "\ndocker load < "+saved_name.replace("../", "")
            MESSAGE += "\nThe image ID will appear, which can then be used to create a container (sudo permission may be required):"
            MESSAGE += "\ndocker run -it IMAGE_ID bash"
            MESSAGE += "\n\nRun the following command on the image: \n"+' '.join(BOCOM.split(' ')[1::])
            MESSAGE += "\n\nThis is the Dockerfile we used to process your job: \n\n"+DOCK_DOCK

            ec.send_mail_complete(researcher_email, "Succesful MIDAS build", MESSAGE, [])

            return None


        # Deletes the key
        r.delete(UTOK+';'+MIDIR)

        # Saves the docker image and sends the user the dockerfile and a link to the tar ball
        # docker-py documentation was erronous

        img = image.get(IMTAG)
        resp = img.save()

        # Creates a file, recycled everytime the program runs
        saved_name = "image."+hashlib.sha256(str(datetime.datetime.now()).encode('UTF-8')).hexdigest()[:4:]+".tar.gz"
        ff = open(saved_name, 'wb')
        for salmon in resp:
            ff.write(salmon)
        ff.close()

        # Moves the file to reef and deletes the local copy
        requests.post('http://'+os.environ['Reef_IP']+':2001/reef/result_upload/'+os.environ['Reef_Key']+'/'+UTOK, files={"file": open(saved_name, "rb")})
        os.remove(saved_name)
        MESSAGE = Success_Message.replace("DATETIME", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        MESSAGE += "\n\nClick on the following link to obtain a compressed version of the application docker image.\n"
        MESSAGE += "You are welcome to upload the image on dockerhub in order to reduce the future job processing time for the same application (no allocation will be discounted): \n"
        MESSAGE += os.environ["SERVER_IP"]+":5060/boincserver/v2/reef/results/"+UTOK+"/"+saved_name
        MESSAGE += "\n\nDownload the image using the following command:\n"
        MESSAGE += "curl -O "+os.environ["SERVER_IP"]+":5060/boincserver/v2/reef/results/"+UTOK+"/"+saved_name.replace("../", "")
        MESSAGE += "\nThen load the image (sudo permission may be required):"
        MESSAGE += "\ndocker load < "+saved_name.replace("../", "")
        MESSAGE += "\nThe image ID will appear, which can then be used to create a container (sudo permission may be required):"
        MESSAGE += "\ndocker run -it IMAGE_ID bash"
        MESSAGE += "\n\nRun the following command on the image: \n"+' '.join(BOCOM.split(' ')[1::])
        MESSAGE += "\n\nThis is the Dockerfile we used to process your job: \n\n"+DOCK_DOCK
        ec.send_mail_complete(researcher_email, "Succesful MIDAS build", MESSAGE, [])

    except Exception as e:
        print(e)
        r.delete(UTOK+';'+MIDIR)
        # Deletes the unused container
        client.containers.prune()
        MESSAGE = Failure_Message.replace("DATETIME", datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
        MESSAGE += "\n\nDockerfile created below: \n\n"+DOCK_DOCK
        ec.send_mail_complete(researcher_email, "Succesful MIDAS build", MESSAGE, [])


to_be_processed = [] # [[TOKEN, MIDAS directory], ...]

# Finds all the submitted jobs
for possible in r.keys():
    kim = possible.decode('UTF-8')
    if ';' in kim:
        to_be_processed.append([kim.split(';')[0], kim.split(';')[1]])
else:
    if len(to_be_processed) == 0:
        # No new user files to be processed
        sys.exit()



# Goes one by one in the list of submitted files
for HJK in to_be_processed:

    user_tok, dir_midas = HJK
    FILE_LOCATION = "/home/boincadm/project/api/sandbox_files/DIR_"+user_tok+'/'+dir_midas

    # Goes to the file location
    os.chdir(FILE_LOCATION)
    #hti: how to install
    hti_OS = mdr.install_OS('README.txt')
    hti_langs =[mdr.install_language(y, mdr.valid_OS('README.txt')) for y in mdr.valid_language('README.txt')]
    hti_setup = mdr.user_guided_setup('README.txt')
    hti_libs = mdr.install_libraries('README.txt')

    # Obtains the commands to run
    ALL_COMS = mdr.present_input_files('.')
    FINAL_COMMANDS = []
    for acom in ALL_COMS:

        # Other languages
        FINAL_COMMANDS.append(mdr.execute_command(acom))


    # All dockerfiles are named the same way {TOKEN}:{10 char hash}
    namran = hashlib.sha256(str(datetime.datetime.now()).encode('UTF-8')).hexdigest()[:10:]
    DTAG = (user_tok+':'+namran).lower().replace('@', '')

    # Composes the dockerfile
    duck = hti_OS+"\n\n\n"+"\n".join(mdr.copy_files_to_image('.'))
    duck += "\nRUN cd /work && "+" && ".join(hti_langs)
    for inst_set in [hti_libs, hti_setup]:
        if inst_set == []:
            continue
        duck += " &&\\\n    "+" && ".join(inst_set)


    duck += "\n\nWORKDIR /work"

    # Actual command
    BOINC_COMMAND = DTAG+" /bin/bash -c  \"cd /work; "+"; ".join(FINAL_COMMANDS)+"; python3 /Mov_Specific.py\""
    # Prints the result to files
    with open("Dockerfile", "w") as DOCKERFILE:
        DOCKERFILE.write(duck)
    with open(namran+".txt", "w") as COMFILE:
        COMFILE.write(BOINC_COMMAND+'\n'+user_tok)

    complete_build(DTAG, user_tok, dir_midas, namran, duck, BOINC_COMMAND)

    # Goes one directory up and deletes the MIDAS folder for space concerns
    os.chdir("..")
    shutil.rmtree(FILE_LOCATION)
