from flask import Flask, render_template, request, redirect, url_for, flash, session
import firebase_admin
from firebase_admin import credentials, firestore, messaging
import datetime
import pytz
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
import base64
import os
from dotenv import load_dotenv

# Charger les variables d'environnement depuis un fichier .env
load_dotenv()

# Initialisation de Flask
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')

# Initialisation de Firebase
def initialize_firebase():
    if not firebase_admin._apps:
        cred = credentials.Certificate(os.getenv('FIREBASE_KEY_PATH'))
        firebase_admin.initialize_app(cred)
    return firestore.client()

db = initialize_firebase()

# Configuration SMTP
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_EMAIL = os.getenv('SMTP_EMAIL')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD')

# Configuration API Orange Cameroun
ORANGE_CLIENT_ID = os.getenv('ORANGE_CLIENT_ID')
ORANGE_CLIENT_SECRET = os.getenv('ORANGE_CLIENT_SECRET')
ORANGE_AUTH_HEADER = os.getenv('ORANGE_AUTH_HEADER')

# Fonction pour obtenir le token d'accès Orange
def obtenir_token_orange():
    url = "https://api.orange.com/oauth/v3/token"
    headers = {
        "Authorization": ORANGE_AUTH_HEADER,
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "client_credentials"
    }
    response = requests.post(url, headers=headers, data=data)
    response_data = response.json()
    return response_data.get("access_token")

# Fonction pour envoyer un SMS via Orange
def envoyer_sms_orange(destinataire, message):
    try:
        token = obtenir_token_orange()
        url = f"https://api.orange.com/smsmessaging/v1/outbound/tel%3A%2B237696875895/requests"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        data = {
            "outboundSMSMessageRequest": {
                "address": f"tel:+{destinataire}",
                "senderAddress": "tel:+237696875895",
                "outboundSMSTextMessage": {
                    "message": message
                }
            }
        }
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 201:
            return f"✅ SMS correctement envoyé à {destinataire}"
        else:
            return f"❌ Erreur lors de l'envoi du SMS à {destinataire} : {response.text}"
    except Exception as e:
        return f"❌ Erreur lors de l'envoi du SMS à {destinataire} : {e}"

# Fonction pour envoyer un email
def envoyer_email(destinataire, sujet, message):
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_EMAIL
        msg['To'] = destinataire
        msg['Subject'] = sujet
        msg.attach(MIMEText(message, 'plain'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.sendmail(SMTP_EMAIL, destinataire, msg.as_string())
        server.quit()
        return f"✅ E-mail correctement envoyé à {destinataire}"
    except Exception as e:
        return f"❌ Erreur lors de l'envoi de l'e-mail à {destinataire} : {e}"

# Fonction pour envoyer une notification FCM
def envoyer_notification_fcm(token, titre, message):
    try:
        print(f"📨 Envoi d'une notification FCM à {token}...")
        message = messaging.Message(
            notification=messaging.Notification(
                title=titre,
                body=message,
            ),
            token=token
        )
        response = messaging.send(message)
        return f"✅ Notification envoyée avec succès : {response}"
    except Exception as e:
        return f"❌ Erreur d'envoi de la notification : {e}"

# Vérification des permissions
def est_bailleur():
    return session.get('role') == 'bailleur'

# Page d'accueil
@app.route('/')
def index():
    return render_template('index.html')

# Route de connexion
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login = request.form['login']
        password = request.form['password']

        # Vérification pour les bailleurs
        utilisateurs_docs = db.collection('utilisateurs').where("email", "==", login).where("password", "==", password).stream()
        utilisateur = None
        for doc in utilisateurs_docs:
            utilisateur = doc.to_dict()
            utilisateur["id"] = doc.id  # Stocker l'ID Firestore

        if utilisateur:
            session['user_id'] = utilisateur["id"]
            session['user_email'] = utilisateur["email"]
            session['role'] = utilisateur["role"]
            flash(f"Bienvenue {utilisateur['email']} !", "success")
            return redirect(url_for('dashboard'))

        # Vérification pour les locataires
        locataires_docs = db.collection('locataires').where("telephone", "==", password).stream()
        locataire = None
        for doc in locataires_docs:
            locataire_data = doc.to_dict()
            if locataire_data["nom"] == login or locataire_data["email"] == login:
                locataire = locataire_data
                locataire["id"] = doc.id  # Stocker l'ID Firestore
                break

        if locataire:
            session['locataire_id'] = locataire["id"]
            session['locataire_nom'] = locataire["nom"]
            flash(f"Bienvenue {locataire['nom']} !", "success")
            return redirect(url_for('locataire_dashboard'))

        flash("Identifiants incorrects", "danger")
        return redirect(url_for('login'))

    return render_template('login.html')

# Route pour le tableau de bord
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        flash("Veuillez vous connecter d'abord.", "warning")
        return redirect(url_for('login'))

    config_ref = db.collection('config').document('parametres')
    config_data = config_ref.get().to_dict() or {}

    total_biens = config_data.get('total_biens', 0)
    total_logements = config_data.get('total_logements', 0)

    locataires_docs = db.collection('locataires').stream()
    total_locataires = sum(1 for _ in locataires_docs)

    paiements_docs = db.collection('paiements').stream()
    paiements = [doc.to_dict() for doc in paiements_docs]

    total_encaisse = sum(p['montant'] for p in paiements if p.get('statut') == 'payé')
    total_impaye = sum(p['montant'] for p in paiements if p.get('statut') == 'impayé')

    taux_occupation = (total_locataires / total_logements) * 100 if total_logements else 0

    return render_template(
        'dashboard.html',
        total_encaisse=total_encaisse,
        total_impaye=total_impaye,
        total_locataires=total_locataires,
        total_logements=total_logements,
        taux_occupation=round(taux_occupation, 2)
    )

# Route de connexion pour les locataires
@app.route('/locataire_login', methods=['GET', 'POST'])
def locataire_login():
    if request.method == 'POST':
        nom = request.form['nom'].strip()
        telephone = request.form['telephone'].strip()

        # Vérification dans Firestore
        locataires_docs = db.collection('locataires').where("nom", "==", nom).where("telephone", "==", telephone).stream()
        locataire = None
        for doc in locataires_docs:
            locataire = doc.to_dict()
            locataire["id"] = doc.id  # Stocker l'ID Firestore

        if locataire:
            session['locataire_id'] = locataire["id"]
            session['locataire_nom'] = locataire["nom"]
            flash(f"Bienvenue {locataire['nom']} !", "success")
            return redirect(url_for('locataire_dashboard'))
        else:
            flash("Nom ou téléphone incorrect", "danger")

    return render_template('locataire_login.html')

# Route pour le tableau de bord du locataire
@app.route('/locataire_dashboard')
def locataire_dashboard():
    if 'locataire_id' not in session:
        flash("Veuillez vous connecter d'abord.", "warning")
        return redirect(url_for('locataire_login'))

    locataire_id = session['locataire_id']
    locataire_nom = session['locataire_nom']

    # Récupération des paiements du locataire
    paiements_docs = db.collection('paiements').where("locataire_id", "==", locataire_id).stream()
    paiements = [{"id": doc.id, **doc.to_dict()} for doc in paiements_docs]

    return render_template('locataire_dashboard.html', locataire_nom=locataire_nom, paiements=paiements)

# Route pour déconnexion locataire
@app.route('/locataire_logout')
def locataire_logout():
    session.clear()
    flash("Déconnexion réussie.", "info")
    return redirect(url_for('locataire_login'))

# Configuration
@app.route('/configuration', methods=['GET', 'POST'])
def configuration():
    config_ref = db.collection('config').document('parametres')
    config_data = config_ref.get().to_dict() or {}

    if request.method == 'POST':
        montant_reparation = request.form.get('montant_reparation', '0')
        montant_reparation = float(montant_reparation) if montant_reparation else 0.0

        config_ref.set({
            'total_biens': int(request.form.get('total_biens', 0)),
            'total_logements': int(request.form.get('total_logements', 0)),
            'immeuble': request.form.get('immeuble', ''),
            'date_entree': request.form.get('date_entree', ''),
            'description_reparation': request.form.get('description_reparation', ''),
            'montant_reparation': montant_reparation,
            'smtp_email': request.form.get('smtp_email', ''),
            'smtp_password': request.form.get('smtp_password', ''),
            'duree_location': int(request.form.get('duree_location', 12))
        })
        flash("✅ Configuration mise à jour avec succès", "success")
        return redirect(url_for('configuration'))

    return render_template('configuration.html', config=config_data)

# Gestion des utilisateurs
@app.route('/utilisateurs')
def utilisateurs():
    users_docs = db.collection('utilisateurs').stream()
    users = [{"id": doc.id, **doc.to_dict()} for doc in users_docs]
    return render_template('utilisateurs.html', users=users)

@app.route('/ajouter_utilisateur', methods=['POST'])
def ajouter_utilisateur():
    db.collection('utilisateurs').add({
        'email': request.form['email'],
        'password': request.form['password'],
        'role': request.form['role']
    })
    flash("Utilisateur ajouté avec succès")
    return redirect(url_for('utilisateurs'))

@app.route('/supprimer_utilisateur/<user_id>', methods=['POST'])
def supprimer_utilisateur(user_id):
    db.collection('utilisateurs').document(user_id).delete()
    flash("Utilisateur supprimé avec succès")
    return redirect(url_for('utilisateurs'))

# Gestion des locataires
@app.route('/locataires')
def locataires():
    locataires_docs = db.collection('locataires').stream()
    locataires = [{"id": doc.id, **doc.to_dict()} for doc in locataires_docs]
    return render_template('locataires.html', locataires=locataires)

@app.route('/ajouter_locataire', methods=['GET', 'POST'])
def ajouter_locataire():
    if request.method == 'POST':
        db.collection('locataires').add({
            'nom': request.form.get('nom'),
            'email': request.form.get('email'),
            'telephone': request.form.get('telephone'),
            'loyer': float(request.form.get('loyer')),
            'date_entree': None
        })
        flash("✅ Locataire ajouté avec succès", "success")
        return redirect(url_for('locataires'))
    return render_template('ajouter_locataire.html')

@app.route('/supprimer_locataire/<locataire_id>', methods=['POST'])
def supprimer_locataire(locataire_id):
    db.collection('locataires').document(locataire_id).delete()
    flash("Locataire supprimé avec succès")
    return redirect(url_for('locataires'))

# Route pour la date d'entrée des locataires
@app.route('/date_entree_locataire', methods=['GET', 'POST'])
def date_entree_locataire():
    locataires_docs = db.collection('locataires').stream()
    locataires = [{"id": doc.id, **doc.to_dict()} for doc in locataires_docs]

    if request.method == 'POST':
        locataire_id = request.form['locataire_id']
        date_entree = request.form['date_entree']

        locataire_ref = db.collection('locataires').document(locataire_id)
        if locataire_ref.get().exists:
            locataire_ref.update({'date_entree': date_entree})
            flash("✅ Date d'entrée mise à jour avec succès", "success")
        else:
            flash("❌ Erreur : Locataire introuvable !", "danger")

        return redirect(url_for('date_entree_locataire'))

    return render_template('date_entree_locataire.html', locataires=locataires)

# Route pour enregistrer le token FCM
@app.route('/enregistrer_token', methods=['POST'])
def enregistrer_token():
    try:
        data = request.json
        token = data.get("token", "").strip()

        if not token:
            return {"status": "error", "message": "Token FCM manquant"}, 400

        # Vérifier si ce token existe déjà dans Firestore pour éviter les doublons
        existing_tokens = db.collection('locataires').where('fcm_token', '==', token).stream()
        if any(existing_tokens):
            return {"status": "warning", "message": "Token déjà existant"}, 200

        # Trouver un locataire avec un token "PENDING" et lui assigner ce token
        locataires_docs = db.collection('locataires').where('fcm_token', '==', 'PENDING').stream()

        locataire_mis_a_jour = False
        for doc in locataires_docs:
            locataire = doc.to_dict()
            db.collection('locataires').document(doc.id).update({'fcm_token': token})
            print(f"✅ Token FCM mis à jour pour {locataire.get('nom', 'Locataire inconnu')}")
            locataire_mis_a_jour = True
            break  # On ne met à jour qu'un seul locataire "PENDING" et on arrête la boucle

        if not locataire_mis_a_jour:
            return {"status": "error", "message": "Aucun locataire avec un token 'PENDING' trouvé"}, 404

        return {"status": "success", "message": "Token enregistré avec succès"}, 200

    except Exception as e:
        print(f"❌ Erreur lors de l'enregistrement du token : {str(e)}")
        return {"status": "error", "message": "Erreur serveur"}, 500

# Gestion des paiements
@app.route('/paiements')
def paiements():
    paiements_docs = db.collection('paiements').stream()
    paiements = [{"id": doc.id, **doc.to_dict()} for doc in paiements_docs]

    locataires_docs = db.collection('locataires').stream()
    locataires = [{"id": doc.id, **doc.to_dict()} for doc in locataires_docs]

    return render_template('paiements.html', paiements=paiements, locataires=locataires)

@app.route('/ajouter_paiement', methods=['POST'])
def ajouter_paiement():
    try:
        montant_reparation = request.form.get('montant_reparation', '0')
        montant_reparation = float(montant_reparation) if montant_reparation else 0.0

        db.collection('paiements').add({
            'locataire_id': request.form.get('locataire_id'),
            'montant': float(request.form.get('montant')),
            'date_paiement': datetime.datetime.strptime(request.form.get('date_paiement'), '%Y-%m-%d').replace(tzinfo=pytz.utc),
            'statut': request.form.get('statut'),
            'montant_reparation': montant_reparation
        })
        flash("Paiement enregistré avec succès.", "success")
    except Exception as e:
        flash(f"Erreur lors de l'ajout du paiement : {e}", "danger")
    return redirect(url_for('paiements'))

# Envoi de rappels
@app.route('/envoyer_rappels')
def envoyer_rappels():
    locataires_docs = db.collection('locataires').stream()
    today = datetime.datetime.now(pytz.utc)

    logs = []

    for doc in locataires_docs:
        locataire = doc.to_dict()
        date_echeance = today.replace(day=5)
        difference = (date_echeance - today).days

        if difference in [5, 2, 0]:
            message = f"Bonjour {locataire['nom']}, votre loyer de {locataire['loyer']} FCFA est dû le {date_echeance.strftime('%d-%m-%Y')}. Merci de procéder au paiement."
            email_log = envoyer_email(locataire.get('email'), "Rappel de paiement", message)
            sms_log = envoyer_sms_orange(locataire.get('telephone'), message)
            logs.append(email_log)
            logs.append(sms_log)

    return render_template('logs.html', logs=logs)

# Déconnexion
@app.route('/logout')
def logout():
    session.clear()
    flash("Déconnexion réussie")
    return redirect(url_for('index'))

# Lancement du serveur Flask
if __name__ == '__main__':
    app.run(debug=True)
